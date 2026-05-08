"""Analytics transport for the OpenSRE CLI."""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import os
import platform
import queue
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx

from app.analytics.events import Event
from app.cli.wizard.store import get_store_path
from app.constants import LEGACY_OPENSRE_HOME_DIR
from app.constants.posthog import POSTHOG_CAPTURE_API_KEY, POSTHOG_HOST
from app.version import get_version

_CONFIG_DIR = get_store_path().parent
_ANONYMOUS_ID_PATH = _CONFIG_DIR / "anonymous_id"
_FIRST_RUN_PATH = _CONFIG_DIR / "installed"
_LEGACY_CONFIG_DIR = LEGACY_OPENSRE_HOME_DIR
_LEGACY_ANONYMOUS_ID_PATH = _LEGACY_CONFIG_DIR / "anonymous_id"
_LEGACY_FIRST_RUN_PATH = _LEGACY_CONFIG_DIR / "installed"

_QUEUE_SIZE = 128
_SEND_TIMEOUT = 2.0
_SHUTDOWN_WAIT = 1.0

_EVENT_LOG_ENV_VAR: Final[str] = "OPENSRE_ANALYTICS_LOG_EVENTS"
_EVENT_LOG_FILENAME: Final[str] = "posthog_events.txt"
_EVENT_LOG_MAX_LINES: Final[int] = 1000
_ANONYMOUS_ID_LOCK_WAIT_SECONDS: Final[float] = 0.5
_ANONYMOUS_ID_LOCK_RETRY_SECONDS: Final[float] = 0.01

_FAILURE_LOG_FILENAME: Final[str] = "analytics_errors.log"
_FAILURE_LOG_MAX_BYTES: Final[int] = 64 * 1024
_FALLBACK_FAILURE_LOG_PATH: Path = Path(tempfile.gettempdir()) / _FAILURE_LOG_FILENAME
_HOME_PATH_RE: Final[re.Pattern[str]] = re.compile(r"/(?:Users|home)/[^/\s]+")
_FAILURE_MESSAGE_MAX_LEN: Final[int] = 240
_COMPOSITE_FINGERPRINT_VERSION: Final[str] = "hashed-local-v1"
_COMPOSITE_FINGERPRINT_NAMESPACE: Final[str] = "opensre-cli-analytics-fingerprint"
_CI_FINGERPRINT_ENV_KEYS: Final[tuple[str, ...]] = (
    "GITHUB_REPOSITORY",
    "GITHUB_RUNNER_NAME",
    "GITHUB_WORKFLOW",
    "GITLAB_PROJECT_PATH",
    "CI_PROJECT_PATH",
    "CIRCLE_PROJECT_USERNAME",
    "CIRCLE_PROJECT_REPONAME",
    "BUILDKITE_ORGANIZATION_SLUG",
    "BUILDKITE_PIPELINE_SLUG",
    "JENKINS_URL",
    "JOB_NAME",
)

type PropertyValue = str | bool | int | float
type Properties = dict[str, PropertyValue]


@dataclass(frozen=True, slots=True)
class _Envelope:
    event: str
    properties: Properties


@dataclass(frozen=True, slots=True)
class _AnonymousIdentity:
    distinct_id: str
    persistence: str


@dataclass(frozen=True, slots=True)
class _CompositeFingerprint:
    value: str
    components: str


_anonymous_id_lock = threading.Lock()
_cached_anonymous_id: str | None = None
_cached_identity_persistence = "unknown"
_first_run_marker_created_this_process = False
_pending_user_id_load_failures: list[Properties] = []
_ONE_TIME_EVENTS: Final[frozenset[str]] = frozenset({Event.INSTALL_DETECTED.value})


def _is_opted_out() -> bool:
    return (
        os.getenv("OPENSRE_NO_TELEMETRY", "0") == "1"
        or os.getenv("OPENSRE_ANALYTICS_DISABLED", "0") == "1"
        or os.getenv("DO_NOT_TRACK", "0") == "1"
    )


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _is_existing_install(
    *,
    config_dir_existed: bool,
    install_marker_existed: bool,
    legacy_config_dir_existed: bool,
    legacy_install_marker_existed: bool,
) -> bool:
    return (
        (config_dir_existed and install_marker_existed)
        or (legacy_config_dir_existed and legacy_install_marker_existed)
    ) and not _first_run_marker_created_this_process


def _queue_user_id_load_failure(
    reason: str,
    *,
    config_dir_existed: bool,
    install_marker_existed: bool,
    anonymous_id_path_existed: bool,
    legacy_config_dir_existed: bool,
    legacy_install_marker_existed: bool,
    legacy_anonymous_id_path_existed: bool,
) -> None:
    if not _is_existing_install(
        config_dir_existed=config_dir_existed,
        install_marker_existed=install_marker_existed,
        legacy_config_dir_existed=legacy_config_dir_existed,
        legacy_install_marker_existed=legacy_install_marker_existed,
    ):
        return
    _pending_user_id_load_failures.append(
        {
            "reason": reason,
            "config_dir": "~/.config/opensre",
            "anonymous_id_path": "~/.config/opensre/anonymous_id",
            "config_dir_existed": config_dir_existed,
            "install_marker_existed": install_marker_existed,
            "anonymous_id_path_existed": anonymous_id_path_existed,
            "legacy_config_dir_existed": legacy_config_dir_existed,
            "legacy_install_marker_existed": legacy_install_marker_existed,
            "legacy_anonymous_id_path_existed": legacy_anonymous_id_path_existed,
            "anonymous_id_loaded": False,
        }
    )


def _pop_user_id_load_failures() -> list[Properties]:
    failures = list(_pending_user_id_load_failures)
    _pending_user_id_load_failures.clear()
    return failures


def _valid_anonymous_id(value: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return str(uuid.UUID(stripped))
    except ValueError:
        return None


def _read_persisted_anonymous_id(path: Path) -> str | None:
    return _valid_anonymous_id(path.read_text(encoding="utf-8"))


def _load_legacy_anonymous_id() -> str | None:
    if not _path_exists(_LEGACY_ANONYMOUS_ID_PATH):
        return None
    with contextlib.suppress(OSError):
        legacy_id = _read_persisted_anonymous_id(_LEGACY_ANONYMOUS_ID_PATH)
        if legacy_id is not None:
            with contextlib.suppress(OSError):
                _write_text_atomic(_ANONYMOUS_ID_PATH, legacy_id)
            return legacy_id
    return None


def _fsync_parent_dir(path: Path) -> None:
    """Best-effort directory fsync so atomic renames survive process crashes on Unix."""
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _write_text_atomic(path: Path, text: str) -> None:
    """Atomically replace ``path`` with ``text`` using a unique sibling temp file."""
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        _fsync_parent_dir(path)
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


@contextlib.contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    deadline = time.monotonic() + _ANONYMOUS_ID_LOCK_WAIT_SECONDS
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for {lock_path}") from None
            time.sleep(_ANONYMOUS_ID_LOCK_RETRY_SECONDS)
        else:
            try:
                os.write(fd, f"{os.getpid()}\n".encode())
                os.fsync(fd)
            except OSError:
                with contextlib.suppress(OSError):
                    lock_path.unlink()
                raise
            finally:
                os.close(fd)
            break

    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()


def _write_new_anonymous_id(
    new_id: str,
    *,
    replace_existing_invalid: bool = False,
) -> _AnonymousIdentity:
    lock_path = _ANONYMOUS_ID_PATH.with_name(f"{_ANONYMOUS_ID_PATH.name}.lock")
    try:
        with _file_lock(lock_path):
            if _ANONYMOUS_ID_PATH.exists():
                existing = _read_persisted_anonymous_id(_ANONYMOUS_ID_PATH)
                if existing is not None:
                    return _AnonymousIdentity(existing, "disk")
                if not replace_existing_invalid:
                    return _AnonymousIdentity(new_id, "none")
            _write_text_atomic(_ANONYMOUS_ID_PATH, new_id)
        return _AnonymousIdentity(new_id, "disk")
    except OSError:
        return _AnonymousIdentity(new_id, "none")


def _compute_anonymous_identity() -> _AnonymousIdentity:
    config_dir_existed = _path_exists(_CONFIG_DIR)
    install_marker_existed = _path_exists(_FIRST_RUN_PATH)
    legacy_config_dir_existed = _path_exists(_LEGACY_CONFIG_DIR)
    legacy_install_marker_existed = _path_exists(_LEGACY_FIRST_RUN_PATH)
    legacy_anonymous_id_path_existed = _path_exists(_LEGACY_ANONYMOUS_ID_PATH)
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        anonymous_id_path_existed = _ANONYMOUS_ID_PATH.exists()
        if anonymous_id_path_existed:
            existing = _read_persisted_anonymous_id(_ANONYMOUS_ID_PATH)
            if existing is not None:
                return _AnonymousIdentity(existing, "disk")
            _queue_user_id_load_failure(
                "invalid_anonymous_id",
                config_dir_existed=config_dir_existed,
                install_marker_existed=install_marker_existed,
                anonymous_id_path_existed=anonymous_id_path_existed,
                legacy_config_dir_existed=legacy_config_dir_existed,
                legacy_install_marker_existed=legacy_install_marker_existed,
                legacy_anonymous_id_path_existed=legacy_anonymous_id_path_existed,
            )
        if legacy_id := _load_legacy_anonymous_id():
            return _AnonymousIdentity(legacy_id, "disk")
        if _is_existing_install(
            config_dir_existed=config_dir_existed,
            install_marker_existed=install_marker_existed,
            legacy_config_dir_existed=legacy_config_dir_existed,
            legacy_install_marker_existed=legacy_install_marker_existed,
        ):
            _queue_user_id_load_failure(
                "missing_anonymous_id",
                config_dir_existed=config_dir_existed,
                install_marker_existed=install_marker_existed,
                anonymous_id_path_existed=anonymous_id_path_existed,
                legacy_config_dir_existed=legacy_config_dir_existed,
                legacy_install_marker_existed=legacy_install_marker_existed,
                legacy_anonymous_id_path_existed=legacy_anonymous_id_path_existed,
            )
        new_id = str(uuid.uuid4())
        return _write_new_anonymous_id(
            new_id,
            replace_existing_invalid=anonymous_id_path_existed,
        )
    except OSError:
        _queue_user_id_load_failure(
            "read_or_write_error",
            config_dir_existed=config_dir_existed,
            install_marker_existed=install_marker_existed,
            anonymous_id_path_existed=_path_exists(_ANONYMOUS_ID_PATH),
            legacy_config_dir_existed=legacy_config_dir_existed,
            legacy_install_marker_existed=legacy_install_marker_existed,
            legacy_anonymous_id_path_existed=legacy_anonymous_id_path_existed,
        )
        return _AnonymousIdentity(str(uuid.uuid4()), "none")


def _get_or_create_anonymous_id() -> str:
    global _cached_anonymous_id, _cached_identity_persistence
    if _cached_anonymous_id is not None:
        return _cached_anonymous_id
    with _anonymous_id_lock:
        if _cached_anonymous_id is None:
            identity = _compute_anonymous_identity()
            _cached_anonymous_id = identity.distinct_id
            _cached_identity_persistence = identity.persistence
        return _cached_anonymous_id


def _identity_persistence() -> str:
    return _cached_identity_persistence


def _event_insert_id(event: str, distinct_id: str) -> str | None:
    if event not in _ONE_TIME_EVENTS:
        return None
    return f"{event}:{distinct_id}"


def _touch_once(path: Path) -> bool:
    global _first_run_marker_created_this_process
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        _fsync_parent_dir(path)
        if path == _FIRST_RUN_PATH:
            _first_run_marker_created_this_process = True
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def _cli_version() -> str:
    return get_version()


def _normalized_fingerprint_value(value: object) -> str | None:
    normalized = str(value).strip().casefold()
    return normalized or None


def _add_fingerprint_component(
    components: dict[str, str],
    component_sources: set[str],
    key: str,
    value: object,
    source: str,
) -> None:
    if normalized := _normalized_fingerprint_value(value):
        components[key] = normalized
        component_sources.add(source)


def _env_first(*keys: str) -> str | None:
    for key in keys:
        if value := _normalized_fingerprint_value(os.getenv(key, "")):
            return value
    return None


def _build_composite_fingerprint() -> _CompositeFingerprint:
    components: dict[str, str] = {}
    component_sources: set[str] = set()

    _add_fingerprint_component(
        components, component_sources, "os_family", platform.system(), "platform"
    )
    _add_fingerprint_component(
        components, component_sources, "machine", platform.machine(), "platform"
    )
    _add_fingerprint_component(components, component_sources, "host", platform.node(), "host")
    if user := _env_first("USER", "LOGNAME", "USERNAME"):
        _add_fingerprint_component(components, component_sources, "user", user, "user")
    with contextlib.suppress(RuntimeError, OSError):
        _add_fingerprint_component(
            components, component_sources, "home_name", Path.home().name, "user"
        )
    for key in _CI_FINGERPRINT_ENV_KEYS:
        if value := _normalized_fingerprint_value(os.getenv(key, "")):
            components[f"env:{key.casefold()}"] = value
            component_sources.add("ci")

    # Do not emit raw host/user/CI data. Hash sorted key-value pairs so the
    # fingerprint is stable while staying one-way in analytics.
    payload = "\n".join(
        [
            _COMPOSITE_FINGERPRINT_NAMESPACE,
            *(f"{key}={components[key]}" for key in sorted(components)),
        ]
    )
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return _CompositeFingerprint(
        value=fingerprint,
        components=",".join(sorted(component_sources)) or "none",
    )


def _event_logging_enabled() -> bool:
    """Whether the local event log is on. Default is enabled.

    Set ``OPENSRE_ANALYTICS_LOG_EVENTS=0`` to disable. Any value other than
    ``"0"`` (including unset, ``"1"``, ``"true"``, etc.) leaves it on.
    """
    return os.getenv(_EVENT_LOG_ENV_VAR, "1") != "0"


def _format_property(key: str, value: PropertyValue) -> str:
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = json.dumps(value, ensure_ascii=False)
    return f"{key}={rendered}"


@dataclass(slots=True)
class _EventLogState:
    initialized: bool = False
    lines_written: int = 0


_event_log_lock = threading.Lock()
_event_log_state = _EventLogState()


def _event_log_path() -> Path:
    """Resolve the event log path lazily so tests can monkeypatch ``_CONFIG_DIR``.

    The log lives next to ``anonymous_id`` and ``analytics_errors.log`` under
    ``~/.config/opensre/`` (or the equivalent on other platforms) rather than
    in the user's current working directory. This prevents a stray
    ``posthog_events.txt`` from showing up in every shell where the user runs
    ``opensre``, and keeps related telemetry artifacts in one place.
    """
    return _CONFIG_DIR / _EVENT_LOG_FILENAME


def _initialize_event_log_state(log_path: Path) -> None:
    """Seed the event-log line count from the existing file on disk.

    Called once per process under ``_event_log_lock``. Honoring pre-existing
    content keeps the cap meaningful across runs — without this seed, a user
    whose file already had 1500 lines from a previous process would write a
    further 1000 before the first rotation, growing the file to 2500 lines.
    """
    if _event_log_state.initialized:
        return
    _event_log_state.initialized = True
    with contextlib.suppress(OSError):
        if log_path.exists():
            with log_path.open("r", encoding="utf-8") as fh:
                _event_log_state.lines_written = sum(1 for _ in fh)


def _rotate_event_log(log_path: Path) -> None:
    """Move the live event log aside so the next write starts fresh.

    Uses rename rather than truncate so the most recent ``_EVENT_LOG_MAX_LINES``
    lines are still inspectable in ``<filename>.1`` after rotation.
    """
    backup = log_path.with_name(log_path.name + ".1")
    with contextlib.suppress(OSError):
        if backup.exists():
            backup.unlink()
    with contextlib.suppress(OSError):
        log_path.rename(backup)


def _append_log_line(line: str) -> None:
    """Append one line to the event log, rotating when the line cap is reached.

    Thread-safe: serialized by ``_event_log_lock`` so concurrent callers cannot
    interleave a write with a rename. Failures (e.g. read-only filesystem) are
    swallowed — the event log is a best-effort developer aid, not a guarantee.
    """
    log_path = _event_log_path()
    with _event_log_lock:
        _initialize_event_log_state(log_path)
        # ``_CONFIG_DIR`` may not yet exist on a fresh install, and ``open("a")``
        # on a missing parent dir raises. Suppress because failures here are
        # already non-fatal — see ``_append_log_line`` docstring.
        with contextlib.suppress(OSError):
            log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            return
        _event_log_state.lines_written += 1
        if _event_log_state.lines_written >= _EVENT_LOG_MAX_LINES:
            _rotate_event_log(log_path)
            _event_log_state.lines_written = 0


def _log_event_line(event: str, properties: Properties) -> None:
    if not _event_logging_enabled():
        return
    timestamp = datetime.now(UTC).isoformat()
    parts = [timestamp, event, *(_format_property(k, v) for k, v in properties.items())]
    _append_log_line(" ".join(parts) + "\n")


def _log_debug_line(message: str) -> None:
    """Append a non-event diagnostic line (e.g. send retries before exhaustion).

    Only emitted when ``OPENSRE_ANALYTICS_LOG_EVENTS=1`` so the cost is opt-in.
    Use ``_log_failure`` instead for terminal failures that must always be
    recorded.
    """
    if not _event_logging_enabled():
        return
    timestamp = datetime.now(UTC).isoformat()
    _append_log_line(f"{timestamp} {message}\n")


def _scrub_error_message(message: str) -> str:
    """Strip user-identifying paths and cap length for safe persistence."""
    scrubbed = _HOME_PATH_RE.sub("~", message)
    if len(scrubbed) > _FAILURE_MESSAGE_MAX_LEN:
        scrubbed = scrubbed[:_FAILURE_MESSAGE_MAX_LEN] + "..."
    return scrubbed


def _format_failure_extra(value: object) -> PropertyValue:
    if isinstance(value, bool):
        return value
    return str(value)


def _failure_breadcrumb_line(stage: str, error: BaseException, extra: dict[str, object]) -> str:
    timestamp = datetime.now(UTC).isoformat()
    parts = [
        timestamp,
        _format_property("stage", stage),
        _format_property("error_type", type(error).__name__),
        _format_property("error_message", _scrub_error_message(str(error))),
    ]
    parts.extend(
        _format_property(key, _format_failure_extra(value)) for key, value in extra.items()
    )
    return " ".join(parts) + "\n"


def _write_failure_line(path: Path, line: str) -> None:
    """Append a failure breadcrumb to ``path`` with naive size-based rotation.

    Rotation uses truncation rather than rename-and-restart because we do not
    care about historical breadcrumbs once the file gets large — the goal is
    diagnostic context for the most recent failures, not an audit log.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > _FAILURE_LOG_MAX_BYTES:
        path.write_text("", encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _log_failure(stage: str, error: BaseException, **extra: object) -> None:
    """Persist a telemetry failure breadcrumb regardless of env configuration.

    Writes a single key=value line to ``<config_dir>/analytics_errors.log`` and
    falls back to ``$TMPDIR/analytics_errors.log`` when the config dir is
    unwritable (the most common cause of init failures). All exceptions are
    swallowed — the telemetry layer must never crash the CLI, even when its
    own diagnostics are broken.

    Mirrored to the opt-in debug log so developers running with
    ``OPENSRE_ANALYTICS_LOG_EVENTS=1`` see failures inline with events.
    """
    line = _failure_breadcrumb_line(stage, error, extra)

    primary_path = _CONFIG_DIR / _FAILURE_LOG_FILENAME
    primary_failed = False
    try:
        _write_failure_line(primary_path, line)
    except OSError:
        primary_failed = True
    except Exception:
        # Defensive: an unexpected exception in our diagnostics path must not
        # propagate. Treat it the same as an OSError and try the fallback.
        primary_failed = True

    if primary_failed:
        with contextlib.suppress(Exception):
            _write_failure_line(_FALLBACK_FAILURE_LOG_PATH, line)

    _log_debug_line(
        f"failure stage={stage} error_type={type(error).__name__} "
        f"error_message={_scrub_error_message(str(error))!r}"
    )


def _capture_sentry_failure(error: BaseException) -> None:
    """Report telemetry failures without making analytics depend on Sentry imports."""
    try:
        from app.utils.sentry_sdk import capture_exception
    except Exception:
        return
    capture_exception(error)


class _QueueOverflow(RuntimeError):
    """Synthetic exception used so ``queue.Full`` produces a useful breadcrumb."""


class _InvalidPropertyValue(TypeError):
    """Raised internally when a caller submits a property value we cannot serialize."""


def _coerce_properties(
    event: str,
    properties: Properties | None,
) -> Properties:
    """Return a sanitized copy of ``properties`` enforcing the ``str | bool`` contract.

    PostHog event values are typed as ``str | bool``; a buggy caller could still
    pass a number, ``None``, or an arbitrary object. We accept ``str | bool``
    as-is, drop ``None`` silently, coerce ``int`` and ``float`` to ``str``, and
    drop anything else with a ``_log_failure`` breadcrumb so the misuse stays
    observable without crashing capture.
    """
    if not properties:
        return {}

    coerced: Properties = {}
    for key, value in properties.items():
        if isinstance(value, bool | str):
            coerced[key] = value
        elif value is None:
            continue
        elif isinstance(value, int | float):
            coerced[key] = str(value)
        else:
            _log_failure(
                "invalid_property",
                _InvalidPropertyValue(
                    f"property {key!r} has unsupported type {type(value).__name__}"
                ),
                event=event,
                property_key=key,
                value_type=type(value).__name__,
            )
    return coerced


_COMPOSITE_FINGERPRINT = _build_composite_fingerprint()

_BASE_PROPERTIES: Final[Properties] = {
    "cli_version": _cli_version(),
    "python_version": platform.python_version(),
    "os_family": platform.system().lower(),
    "os_version": platform.release(),
    "composite_fingerprint": _COMPOSITE_FINGERPRINT.value,
    "composite_fingerprint_version": _COMPOSITE_FINGERPRINT_VERSION,
    "composite_fingerprint_components": _COMPOSITE_FINGERPRINT.components,
    "$process_person_profile": False,
}


class Analytics:
    def __init__(self) -> None:
        self._disabled = _is_opted_out()
        self._anonymous_id = _get_or_create_anonymous_id()
        self._identity_persistence = _identity_persistence()
        self._queue: queue.Queue[_Envelope | None] = queue.Queue(maxsize=_QUEUE_SIZE)
        self._pending_lock = threading.Lock()
        self._pending = 0
        self._drained = threading.Event()
        self._drained.set()
        self._worker: threading.Thread | None = None
        self._shutdown = False
        self._worker_alive = not self._disabled

        if not self._disabled:
            atexit.register(self.shutdown)
            for properties in _pop_user_id_load_failures():
                self.capture(Event.USER_ID_LOAD_FAILED, properties)

    def capture(self, event: Event, properties: Properties | None = None) -> None:
        if self._disabled or self._shutdown:
            return
        envelope = _Envelope(
            event=event.value,
            properties=_BASE_PROPERTIES | _coerce_properties(event.value, properties),
        )
        pending_registered = False
        try:
            self._ensure_worker()
            with self._pending_lock:
                self._pending += 1
                pending_registered = True
                self._drained.clear()
            self._queue.put_nowait(envelope)
        except queue.Full:
            self._mark_done()
            error = _QueueOverflow(f"queue overflow at size={_QUEUE_SIZE}")
            _log_failure("queue_full", error, event=event.value)
            _capture_sentry_failure(error)
        except Exception as exc:
            if pending_registered:
                self._mark_done()
            _log_failure("capture", exc, event=event.value)
            _capture_sentry_failure(exc)

    def shutdown(self, *, flush: bool = True, timeout: float = _SHUTDOWN_WAIT) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        if self._worker_alive:
            try:
                self._ensure_worker()
            except Exception as exc:
                _log_failure("worker_start", exc)
                _capture_sentry_failure(exc)
                self._worker_alive = False
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        if flush and self._worker is not None:
            self._drained.wait(timeout=timeout)
            self._worker.join(timeout=timeout)

    def _ensure_worker(self) -> None:
        if self._worker is not None:
            return
        worker = threading.Thread(target=self._worker_loop, name="opensre-analytics", daemon=True)
        worker.start()
        self._worker = worker

    def _worker_loop(self) -> None:
        with httpx.Client(timeout=_SEND_TIMEOUT, trust_env=False) as client:
            while True:
                item = self._queue.get()
                if item is None:
                    self._queue.task_done()
                    break
                try:
                    self._send(client, item)
                finally:
                    self._queue.task_done()
                    self._mark_done()
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    if item is not None:
                        self._send(client, item)
                finally:
                    self._queue.task_done()
                    self._mark_done()

    def _send(self, client: httpx.Client, item: _Envelope) -> None:
        properties: Properties = {
            **item.properties,
            "distinct_id": self._anonymous_id,
            "$lib": "opensre-cli",
            "identity_persistence": self._identity_persistence,
        }
        insert_id = _event_insert_id(item.event, self._anonymous_id)
        if insert_id is not None:
            properties["$insert_id"] = insert_id
        _log_event_line(item.event, properties)
        payload = {
            "api_key": POSTHOG_CAPTURE_API_KEY,
            "event": item.event,
            "properties": properties,
        }
        try:
            client.post(f"{POSTHOG_HOST}/capture/", json=payload).raise_for_status()
        except Exception as exc:
            _log_failure("posthog_send", exc, event=item.event)
            _capture_sentry_failure(exc)

    def _mark_done(self) -> None:
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self._drained.set()


_instance: Analytics | None = None


def get_analytics() -> Analytics:
    global _instance
    if _instance is None:
        _instance = Analytics()
    return _instance


def shutdown_analytics(*, flush: bool = True) -> None:
    if _instance is not None:
        _instance.shutdown(flush=flush)


def capture_install_detected_if_needed(properties: Properties | None = None) -> bool:
    """Capture ``install_detected`` once per persisted OpenSRE home."""
    if _path_exists(_FIRST_RUN_PATH):
        return False
    analytics = get_analytics()
    if not _touch_once(_FIRST_RUN_PATH):
        return False
    analytics.capture(Event.INSTALL_DETECTED, properties)
    return True


def capture_first_run_if_needed() -> None:
    capture_install_detected_if_needed()
