"""Sentry SDK initialisation for runtime error monitoring.

Initialises Sentry using the project DSN constant.  Call ``init_sentry()`` once
early in each process entry-point (CLI, LangGraph worker, etc.).  Repeated calls
are safe — the function is idempotent.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from contextlib import suppress
from functools import cache
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.analytics.events import Event
from app.constants import (
    SENTRY_DSN,
    SENTRY_ERROR_SAMPLE_RATE,
    SENTRY_TRACES_SAMPLE_RATE,
)

_HOME_PATH_RE: re.Pattern[str] = re.compile(r"/(?:Users|home)/[^/\s]+")
_SENSITIVE_KEY_SUFFIXES: tuple[str, ...] = ("_token", "_key", "_secret", "_password")
_QUERY_SCRUBBING_CATEGORIES: frozenset[str] = frozenset({"http", "httpx"})


def _is_sentry_disabled() -> bool:
    return (
        os.getenv("OPENSRE_NO_TELEMETRY", "0") == "1"
        or os.getenv("OPENSRE_SENTRY_DISABLED", "0") == "1"
        or os.getenv("DO_NOT_TRACK", "0") == "1"
    )


def _sample_rate_from_env(env_var: str, default: float) -> float:
    try:
        sample_rate = float(os.getenv(env_var, str(default)))
    except ValueError:
        return default
    return min(1.0, max(0.0, sample_rate))


def _resolved_dsn() -> str:
    """Allow env overrides while keeping the bundled DSN as the default."""
    return os.getenv("OPENSRE_SENTRY_DSN") or os.getenv("SENTRY_DSN") or SENTRY_DSN


def _scrub_string(value: object) -> object:
    if isinstance(value, str):
        return _HOME_PATH_RE.sub("~", value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(lowered.endswith(suffix) for suffix in _SENSITIVE_KEY_SUFFIXES)


def _scrub_request(request: dict[str, Any]) -> None:
    headers = request.get("headers")
    if isinstance(headers, dict):
        for header in list(headers):
            if header.lower() in {"authorization", "cookie", "set-cookie", "x-api-key"}:
                headers[header] = "[Filtered]"
    if "cookies" in request:
        request["cookies"] = "[Filtered]"


def _scrub_extra(extra: dict[str, Any]) -> None:
    for key in list(extra):
        if _is_sensitive_key(key):
            extra[key] = "[Filtered]"


def _scrub_stacktrace_frames(frames: list[dict[str, Any]]) -> None:
    for frame in frames:
        for path_key in ("abs_path", "filename"):
            if path_key in frame:
                frame[path_key] = _scrub_string(frame[path_key])
        local_vars = frame.get("vars")
        if isinstance(local_vars, dict):
            for key, value in list(local_vars.items()):
                if _is_sensitive_key(key):
                    local_vars[key] = "[Filtered]"
                else:
                    local_vars[key] = _scrub_string(value)


def _scrub_event_in_place(event: dict[str, Any]) -> None:
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_request(request)

    extra = event.get("extra")
    if isinstance(extra, dict):
        _scrub_extra(extra)

    exception = event.get("exception")
    if isinstance(exception, dict):
        for entry in exception.get("values", []) or []:
            stacktrace = entry.get("stacktrace") if isinstance(entry, dict) else None
            if isinstance(stacktrace, dict):
                frames = stacktrace.get("frames")
                if isinstance(frames, list):
                    _scrub_stacktrace_frames(frames)


def _before_send(event: Any, _hint: dict[str, Any]) -> Any:
    """Drop or scrub a Sentry event before transport.

    Returns ``None`` to drop the event (e.g. when DSN is empty), otherwise
    returns the same dict with sensitive bits replaced with ``[Filtered]``.
    """
    if not _resolved_dsn():
        return None
    if not isinstance(event, dict):
        return event
    try:
        _scrub_event_in_place(event)
    except Exception:  # noqa: BLE001
        # The hook must never raise — Sentry will swallow the event silently.
        return event
    return event


def _strip_url_query(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


def _before_breadcrumb(crumb: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """Strip query strings from HTTP breadcrumbs to avoid leaking secrets."""
    category = crumb.get("category")
    if isinstance(category, str) and category in _QUERY_SCRUBBING_CATEGORIES:
        data = crumb.get("data")
        if isinstance(data, dict):
            url = data.get("url")
            if isinstance(url, str):
                data["url"] = _strip_url_query(url)
    return crumb


def _capture_sentry_init_skipped(reason: str, *, error_type: str | None = None) -> None:
    # Local import to avoid an import cycle between Sentry and analytics modules.
    from app.analytics.provider import Properties, get_analytics

    properties: Properties = {"reason": reason}
    if error_type is not None:
        properties["error_type"] = error_type
    with suppress(Exception):
        get_analytics().capture(Event.SENTRY_INIT_SKIPPED, properties)


@cache
def _init_sentry_once(
    dsn: str,
    environment: str,
    release: str,
    sample_rate: float,
    traces_sample_rate: float,
) -> None:
    """Initialize Sentry once per effective runtime configuration."""
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=False,
        attach_stacktrace=True,
        sample_rate=sample_rate,
        traces_sample_rate=traces_sample_rate,
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
    )


def init_sentry() -> None:
    """Configure and start the Sentry SDK if a DSN is available.

    DSN sourcing precedence: ``OPENSRE_SENTRY_DSN`` env var, ``SENTRY_DSN``
    env var, then the bundled constant. Set ``OPENSRE_NO_TELEMETRY=1`` or
    ``DO_NOT_TRACK=1`` to disable both Sentry and PostHog product analytics.
    ``OPENSRE_SENTRY_DISABLED=1`` disables Sentry only;
    ``OPENSRE_ANALYTICS_DISABLED=1`` disables PostHog only.
    """
    if _is_sentry_disabled():
        _capture_sentry_init_skipped("telemetry_disabled")
        return

    from app.config import get_environment
    from app.version import get_version

    try:
        _init_sentry_once(
            dsn=_resolved_dsn(),
            environment=get_environment().value,
            release=f"opensre@{get_version()}",
            sample_rate=_sample_rate_from_env(
                "SENTRY_ERROR_SAMPLE_RATE",
                SENTRY_ERROR_SAMPLE_RATE,
            ),
            traces_sample_rate=_sample_rate_from_env(
                "SENTRY_TRACES_SAMPLE_RATE",
                SENTRY_TRACES_SAMPLE_RATE,
            ),
        )
    except ModuleNotFoundError:
        _capture_sentry_init_skipped("missing_sdk", error_type="ModuleNotFoundError")
        raise
    except Exception as exc:
        _capture_sentry_init_skipped("init_error", error_type=type(exc).__name__)
        raise


def capture_exception(
    exc: BaseException,
    *,
    context: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Best-effort capture for exceptions swallowed by boundary adapters."""
    if _is_sentry_disabled():
        return
    with suppress(Exception):
        import sentry_sdk

        if context is None and not extra:
            sentry_sdk.capture_exception(exc)
            return
        with sentry_sdk.push_scope() as scope:
            if context is not None:
                scope.set_tag("opensre.context", context)
            if extra:
                for key, value in extra.items():
                    scope.set_extra(key, value)
            sentry_sdk.capture_exception(exc)
