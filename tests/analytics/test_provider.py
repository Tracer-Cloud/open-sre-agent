from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest

from app.analytics import install, provider
from app.analytics.events import Event


@pytest.fixture(autouse=True)
def _reset_anonymous_id_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    provider.shutdown_analytics(flush=False)
    provider._instance = None
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    provider._cached_anonymous_id = None
    provider._cached_identity_persistence = "unknown"
    provider._first_run_marker_created_this_process = False
    provider._pending_user_id_load_failures.clear()
    monkeypatch.setattr(provider, "_event_log_state", provider._EventLogState())
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    legacy_dir = tmp_path / "legacy-opensre"
    monkeypatch.setattr(provider, "_LEGACY_CONFIG_DIR", legacy_dir)
    monkeypatch.setattr(provider, "_LEGACY_ANONYMOUS_ID_PATH", legacy_dir / "anonymous_id")
    monkeypatch.setattr(provider, "_LEGACY_FIRST_RUN_PATH", legacy_dir / "installed")
    yield
    provider.shutdown_analytics(flush=False)
    provider._instance = None


class _StubAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, provider.Properties | None]] = []

    def capture(self, event: Event, properties: provider.Properties | None = None) -> None:
        self.events.append((event, properties))


def _stub_httpx_client(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    posted_payloads: list[dict[str, object]] = []

    class _StubResponse:
        def raise_for_status(self) -> None:
            return None

    class _StubClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            return None

        def post(self, url: str, json: dict[str, object]) -> _StubResponse:
            posted_payloads.append({"url": url, "json": json})
            return _StubResponse()

    monkeypatch.setattr(provider.httpx, "Client", _StubClient)
    return posted_payloads


def test_capture_install_detected_if_needed_captures_once(monkeypatch, tmp_path: Path) -> None:
    stub = _StubAnalytics()
    marker_path = tmp_path / "installed"

    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", marker_path)
    monkeypatch.setattr(provider, "get_analytics", lambda: stub)

    first = provider.capture_install_detected_if_needed({"install_source": "make_install"})
    second = provider.capture_install_detected_if_needed({"install_source": "make_install"})

    assert first is True
    assert second is False
    assert marker_path.exists()
    assert stub.events == [
        (Event.INSTALL_DETECTED, {"install_source": "make_install"}),
    ]


def test_capture_first_run_if_needed_uses_same_install_guard(monkeypatch, tmp_path: Path) -> None:
    stub = _StubAnalytics()

    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider, "get_analytics", lambda: stub)

    provider.capture_first_run_if_needed()
    provider.capture_first_run_if_needed()

    assert stub.events == [(Event.INSTALL_DETECTED, None)]


def test_capture_install_detected_initializes_identity_before_install_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)
    posted_payloads = _stub_httpx_client(monkeypatch)

    captured = provider.capture_install_detected_if_needed(
        {"install_source": "make_install", "entrypoint": "make install"}
    )
    provider.shutdown_analytics(flush=True)

    assert captured is True
    assert (tmp_path / "anonymous_id").exists()
    assert (tmp_path / "installed").exists()
    events = [payload["json"]["event"] for payload in posted_payloads]
    assert events == [Event.INSTALL_DETECTED.value]


def test_get_or_create_anonymous_id_reuses_persisted_value(monkeypatch, tmp_path: Path) -> None:
    anonymous_id_path = tmp_path / "anonymous_id"

    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)

    first = provider._get_or_create_anonymous_id()
    second = provider._get_or_create_anonymous_id()

    assert first == second
    assert anonymous_id_path.read_text(encoding="utf-8") == first


def test_composite_fingerprint_hashes_stable_local_and_ci_signals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(provider.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(provider.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(provider.platform, "node", lambda: "Build-Host-01")
    monkeypatch.setattr(provider.Path, "home", lambda: tmp_path / "jan")
    monkeypatch.setenv("USER", "jan")
    monkeypatch.setenv("GITHUB_REPOSITORY", "opensre/tracer-agent")

    first = provider._build_composite_fingerprint()
    second = provider._build_composite_fingerprint()

    assert first == second
    assert first.components == "ci,host,platform,user"
    assert len(first.value) == 32
    assert "jan" not in first.value
    assert "Build-Host-01" not in first.value
    assert "opensre/tracer-agent" not in first.value


def test_composite_fingerprint_changes_when_stable_machine_identity_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(provider.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(provider.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(provider.platform, "node", lambda: "Build-Host-01")
    monkeypatch.setattr(provider.Path, "home", lambda: tmp_path / "jan")
    monkeypatch.setenv("USER", "jan")
    first = provider._build_composite_fingerprint()

    monkeypatch.setattr(provider.platform, "node", lambda: "Build-Host-02")
    second = provider._build_composite_fingerprint()

    assert second.value != first.value


def test_analytics_reuses_disk_identity_across_process_cache_resets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)

    first = provider.Analytics()
    first_id = first._anonymous_id
    first.shutdown(flush=False)

    provider._cached_anonymous_id = None
    provider._cached_identity_persistence = "unknown"

    second = provider.Analytics()
    second.shutdown(flush=False)

    assert second._anonymous_id == first_id
    assert second._identity_persistence == "disk"


def test_analytics_events_from_same_instance_share_exact_distinct_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)
    posted_payloads = _stub_httpx_client(monkeypatch)

    analytics = provider.Analytics()
    analytics.capture(Event.CLI_INVOKED, {"interactive": False})
    analytics.capture(Event.ONBOARD_STARTED, {"entrypoint": "cli"})
    analytics.capture(Event.INVESTIGATION_COMPLETED)
    analytics.shutdown(flush=True)

    assert len(posted_payloads) == 3
    distinct_ids = [payload["json"]["properties"]["distinct_id"] for payload in posted_payloads]
    assert distinct_ids == [analytics._anonymous_id] * 3
    assert len(set(distinct_ids)) == 1
    log_lines = (tmp_path / "posthog_events.txt").read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 3
    assert Event.CLI_INVOKED.value in log_lines[0]
    assert f'distinct_id="{analytics._anonymous_id}"' in log_lines[0]


def test_existing_install_missing_anonymous_id_captures_posthog_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider, "_LEGACY_CONFIG_DIR", tmp_path / ".opensre")
    monkeypatch.setattr(
        provider, "_LEGACY_ANONYMOUS_ID_PATH", tmp_path / ".opensre" / "anonymous_id"
    )
    monkeypatch.setattr(provider, "_LEGACY_FIRST_RUN_PATH", tmp_path / ".opensre" / "installed")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)
    (tmp_path / "installed").touch()
    posted_payloads = _stub_httpx_client(monkeypatch)

    analytics = provider.Analytics()
    analytics.shutdown(flush=True)

    user_id_errors = [
        payload["json"]
        for payload in posted_payloads
        if payload["json"].get("event") == Event.USER_ID_LOAD_FAILED.value
    ]
    assert len(user_id_errors) == 1
    properties = user_id_errors[0]["properties"]
    assert properties["reason"] == "missing_anonymous_id"
    assert properties["config_dir"] == "~/.config/opensre"
    assert properties["anonymous_id_path"] == "~/.config/opensre/anonymous_id"
    assert properties["config_dir_existed"] is True
    assert properties["install_marker_existed"] is True
    assert properties["anonymous_id_path_existed"] is False
    assert properties["anonymous_id_loaded"] is False


def test_first_run_missing_anonymous_id_does_not_capture_posthog_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)
    assert provider._touch_once(tmp_path / "installed") is True
    posted_payloads = _stub_httpx_client(monkeypatch)

    analytics = provider.Analytics()
    analytics.shutdown(flush=True)

    assert [
        payload["json"]
        for payload in posted_payloads
        if payload["json"].get("event") == Event.USER_ID_LOAD_FAILED.value
    ] == []


def test_legacy_anonymous_id_is_loaded_into_config_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / ".config" / "opensre"
    legacy_dir = tmp_path / ".opensre"
    legacy_id = "11111111-2222-3333-4444-555555555555"
    legacy_dir.mkdir()
    (legacy_dir / "installed").touch()
    (legacy_dir / "anonymous_id").write_text(legacy_id, encoding="utf-8")
    monkeypatch.setattr(provider, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", config_dir / "anonymous_id")
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", config_dir / "installed")
    monkeypatch.setattr(provider, "_LEGACY_CONFIG_DIR", legacy_dir)
    monkeypatch.setattr(provider, "_LEGACY_ANONYMOUS_ID_PATH", legacy_dir / "anonymous_id")
    monkeypatch.setattr(provider, "_LEGACY_FIRST_RUN_PATH", legacy_dir / "installed")

    assert provider._get_or_create_anonymous_id() == legacy_id
    assert (config_dir / "anonymous_id").read_text(encoding="utf-8") == legacy_id
    assert provider._pending_user_id_load_failures == []


def test_anonymous_id_replaces_non_uuid_persisted_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    anonymous_id_path = tmp_path / "anonymous_id"
    anonymous_id_path.write_text("not-a-uuid", encoding="utf-8")
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)

    value = provider._get_or_create_anonymous_id()

    uuid.UUID(value)
    assert value != "not-a-uuid"
    assert anonymous_id_path.read_text(encoding="utf-8") == value


def test_anonymous_id_replaces_empty_persisted_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    anonymous_id_path = tmp_path / "anonymous_id"
    anonymous_id_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)

    value = provider._get_or_create_anonymous_id()

    uuid.UUID(value)
    assert anonymous_id_path.read_text(encoding="utf-8") == value


def test_anonymous_id_permission_error_falls_back_without_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    anonymous_id_path = tmp_path / "anonymous_id"
    anonymous_id_path.write_text("11111111-2222-3333-4444-555555555555", encoding="utf-8")
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)
    real_read_text = Path.read_text

    def _raise_permission_error(self: Path, *args, **kwargs) -> str:
        if self == anonymous_id_path:
            raise PermissionError("permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raise_permission_error)

    value = provider._get_or_create_anonymous_id()

    uuid.UUID(value)
    assert value != "11111111-2222-3333-4444-555555555555"


def test_concurrent_first_runs_converge_on_one_persisted_anonymous_id(tmp_path: Path) -> None:
    start_file = tmp_path / "start"
    repo_root = Path(__file__).resolve().parents[2]
    script = """
import sys
import time
from pathlib import Path

from app.analytics import provider

config_dir = Path(sys.argv[1])
start_file = Path(sys.argv[2])
provider._CONFIG_DIR = config_dir
provider._ANONYMOUS_ID_PATH = config_dir / "anonymous_id"

while not start_file.exists():
    time.sleep(0.001)

print(provider._compute_anonymous_id(), flush=True)
"""
    env = os.environ | {"OPENSRE_ANALYTICS_DISABLED": "1"}
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), str(start_file)],
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(8)
    ]

    start_file.touch()
    completed = [process.communicate(timeout=10) for process in processes]

    for process, (_stdout, stderr) in zip(processes, completed, strict=True):
        assert process.returncode == 0, stderr
    ids = [stdout.strip() for stdout, _stderr in completed]
    assert len(set(ids)) == 1
    assert (tmp_path / "anonymous_id").read_text(encoding="utf-8") == ids[0]
    uuid.UUID(ids[0])


def test_insert_id_is_stable_for_same_one_time_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")

    analytics = provider.Analytics()
    envelope = provider._Envelope(
        event=Event.INSTALL_DETECTED.value,
        properties={},
    )
    posted_payloads = _stub_httpx_client(monkeypatch)
    client = provider.httpx.Client()

    analytics._send(client, envelope)
    analytics._send(client, envelope)
    analytics.shutdown(flush=False)

    insert_ids = [payload["json"]["properties"]["$insert_id"] for payload in posted_payloads]
    assert len(insert_ids) == 2
    assert insert_ids[0] == insert_ids[1] == f"install_detected:{analytics._anonymous_id}"


def test_install_main_reuses_shared_install_guard(monkeypatch) -> None:
    captured: list[provider.Properties | None] = []

    monkeypatch.setattr(
        install,
        "capture_install_detected_if_needed",
        lambda properties=None: captured.append(properties) or True,
    )
    monkeypatch.setattr(install, "shutdown_analytics", lambda **_kwargs: None)

    exit_code = install.main()

    assert exit_code == 0
    assert captured == [{"install_source": "make_install", "entrypoint": "make install"}]


def test_analytics_disabled_when_opensre_analytics_disabled_opt_out(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENSRE_ANALYTICS_DISABLED", "1")
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")

    client_inits = 0

    class _FailIfConstructedClient:
        def __init__(self, *_args, **_kwargs) -> None:
            nonlocal client_inits
            client_inits += 1
            raise AssertionError(
                "httpx client should not be constructed when analytics is disabled"
            )

    monkeypatch.setattr(provider.httpx, "Client", _FailIfConstructedClient)
    analytics = provider.Analytics()
    analytics.capture(Event.INSTALL_DETECTED, {"install_source": "make_install"})

    assert analytics._disabled is True
    assert analytics._worker is None
    assert analytics._pending == 0
    assert analytics._queue.qsize() == 0
    assert client_inits == 0


def test_analytics_disabled_when_do_not_track_opt_out(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")

    client_inits = 0

    class _FailIfConstructedClient:
        def __init__(self, *_args, **_kwargs) -> None:
            nonlocal client_inits
            client_inits += 1
            raise AssertionError(
                "httpx client should not be constructed when analytics is disabled"
            )

    monkeypatch.setattr(provider.httpx, "Client", _FailIfConstructedClient)
    analytics = provider.Analytics()
    analytics.capture(Event.INSTALL_DETECTED, {"install_source": "make_install"})

    assert analytics._disabled is True
    assert analytics._worker is None
    assert analytics._pending == 0
    assert analytics._queue.qsize() == 0
    assert client_inits == 0


def test_get_or_create_anonymous_id_returns_uuid_when_write_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """Test that _get_or_create_anonymous_id returns a UUID when file write fails."""
    anonymous_id_path = tmp_path / "anonymous_id"
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)

    def _raise_oserror(*_args, **_kwargs) -> NoReturn:
        raise OSError("disk write failed")

    monkeypatch.setattr(provider, "_write_text_atomic", _raise_oserror)

    value = provider._get_or_create_anonymous_id()
    assert isinstance(value, str)
    assert value.strip() != ""
    # Verify it's a valid UUID
    uuid.UUID(value)


def test_anonymous_id_replaces_invalid_persisted_value(monkeypatch, tmp_path: Path) -> None:
    anonymous_id_path = tmp_path / "anonymous_id"
    anonymous_id_path.write_text("not-a-uuid", encoding="utf-8")
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)

    value = provider._get_or_create_anonymous_id()

    uuid.UUID(value)
    assert value != "not-a-uuid"
    assert anonymous_id_path.read_text(encoding="utf-8") == value


def test_anonymous_id_concurrent_first_run_creation_uses_one_file_backed_id(
    monkeypatch, tmp_path: Path
) -> None:
    anonymous_id_path = tmp_path / "anonymous_id"
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)

    barrier = threading.Barrier(parties=16)
    results: list[provider._AnonymousIdentity] = []
    results_lock = threading.Lock()

    def _worker() -> None:
        candidate = str(uuid.uuid4())
        barrier.wait()
        identity = provider._write_new_anonymous_id(
            candidate,
            replace_existing_invalid=False,
        )
        with results_lock:
            results.append(identity)

    threads = [threading.Thread(target=_worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert len(results) == 16
    distinct_ids = {identity.distinct_id for identity in results}
    assert len(distinct_ids) == 1
    only_id = next(iter(distinct_ids))
    assert anonymous_id_path.read_text(encoding="utf-8") == only_id
    assert {identity.persistence for identity in results} == {"disk"}


def test_write_new_anonymous_id_adopts_id_from_racing_process(monkeypatch, tmp_path: Path) -> None:
    anonymous_id_path = tmp_path / "anonymous_id"
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_LOCK_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_LOCK_RETRY_SECONDS", 0.001)

    lock_path = tmp_path / "anonymous_id.lock"
    lock_path.write_text("other-process\n", encoding="utf-8")
    winning_id = "11111111-2222-3333-4444-555555555555"

    def _release_racing_lock() -> None:
        time.sleep(0.02)
        anonymous_id_path.write_text(winning_id, encoding="utf-8")
        lock_path.unlink()

    thread = threading.Thread(target=_release_racing_lock)
    thread.start()
    try:
        identity = provider._write_new_anonymous_id("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    finally:
        thread.join(timeout=5.0)

    assert identity == provider._AnonymousIdentity(winning_id, "disk")
    assert anonymous_id_path.read_text(encoding="utf-8") == winning_id


def test_write_text_atomic_replaces_existing_file_and_removes_temp(tmp_path: Path) -> None:
    target = tmp_path / "anonymous_id"
    target.write_text("old-id", encoding="utf-8")

    provider._write_text_atomic(target, "new-id")

    assert target.read_text(encoding="utf-8") == "new-id"
    assert list(tmp_path.glob(".anonymous_id.*.tmp")) == []


def test_install_detected_gets_stable_insert_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)
    posted_payloads = _stub_httpx_client(monkeypatch)

    analytics = provider.Analytics()
    analytics.capture(Event.INSTALL_DETECTED, {"install_source": "make_install"})
    analytics.capture(Event.INSTALL_DETECTED, {"install_source": "make_install"})
    analytics.shutdown(flush=True)

    assert len(posted_payloads) == 2
    insert_ids = {payload["json"]["properties"]["$insert_id"] for payload in posted_payloads}
    assert insert_ids == {f"install_detected:{analytics._anonymous_id}"}


def test_recurring_events_do_not_get_insert_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)
    posted_payloads = _stub_httpx_client(monkeypatch)

    analytics = provider.Analytics()
    analytics.capture(Event.CLI_INVOKED)
    analytics.shutdown(flush=True)

    assert len(posted_payloads) == 1
    assert "$insert_id" not in posted_payloads[0]["json"]["properties"]


def test_identity_persistence_property_marks_none_when_disk_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)

    def _raise_oserror(*_args, **_kwargs) -> NoReturn:
        raise OSError("disk write failed")

    monkeypatch.setattr(provider, "_write_text_atomic", _raise_oserror)
    posted_payloads = _stub_httpx_client(monkeypatch)

    analytics = provider.Analytics()
    analytics.capture(Event.CLI_INVOKED)
    analytics.shutdown(flush=True)

    assert len(posted_payloads) == 1
    assert posted_payloads[0]["json"]["properties"]["identity_persistence"] == "none"


def test_capture_install_detected_if_needed_returns_false_when_marker_write_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """Test that capture_install_detected_if_needed returns False when marker file write fails."""
    stub = _StubAnalytics()
    marker_path = tmp_path / "installed"
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", marker_path)
    monkeypatch.setattr(provider, "get_analytics", lambda: stub)

    real_open = Path.open

    def _raise_oserror(self: Path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self == marker_path and "x" in mode:
            raise OSError("touch failed")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _raise_oserror)

    captured = provider.capture_install_detected_if_needed({"install_source": "make_install"})
    assert captured is False
    assert stub.events == []


def test_capture_install_detected_if_needed_handles_exclusive_create_race(
    monkeypatch, tmp_path: Path
) -> None:
    stub = _StubAnalytics()
    marker_path = tmp_path / "installed"
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", marker_path)
    monkeypatch.setattr(provider, "get_analytics", lambda: stub)

    real_open = Path.open

    def _raise_file_exists(self: Path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self == marker_path and "x" in mode:
            raise FileExistsError("created by another process")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _raise_file_exists)

    captured = provider.capture_install_detected_if_needed({"install_source": "make_install"})

    assert captured is False
    assert stub.events == []


def test_shutdown_is_idempotent_and_capture_after_shutdown_is_noop(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)

    posted_payloads: list[dict[str, object]] = []

    class _StubResponse:
        def raise_for_status(self) -> None:
            return None

    class _StubClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            return None

        def post(self, url: str, json: dict[str, object]) -> _StubResponse:
            posted_payloads.append({"url": url, "json": json})
            return _StubResponse()

    monkeypatch.setattr(provider.httpx, "Client", _StubClient)

    analytics = provider.Analytics()
    analytics.capture(Event.INSTALL_DETECTED, {"install_source": "make_install"})

    analytics.shutdown(flush=True)
    sent_before_post_shutdown_capture = len(posted_payloads)
    pending_before_capture = analytics._pending
    queue_size_before_capture = analytics._queue.qsize()

    analytics.shutdown(flush=False)
    analytics.capture(Event.INSTALL_DETECTED, {"install_source": "make_install"})

    assert analytics._shutdown is True
    assert analytics._pending == pending_before_capture == 0
    assert analytics._queue.qsize() == queue_size_before_capture
    assert len(posted_payloads) == sent_before_post_shutdown_capture == 1


def test_analytics_post_shutdown_capture_is_safe_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)

    analytics = provider.Analytics()
    assert analytics._disabled is False
    analytics.shutdown(flush=False)

    analytics.capture(Event.INSTALL_DETECTED)

    assert analytics._pending == 0


def test_shutdown_analytics_is_noop_when_singleton_not_initialized(monkeypatch) -> None:
    monkeypatch.setattr(provider, "_instance", None)

    provider.shutdown_analytics(flush=False)

    assert provider._instance is None


def test_analytics_is_disabled_when_no_telemetry_env_var_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENSRE_NO_TELEMETRY=1 must opt out; smoke tests rely on it."""
    monkeypatch.setenv("OPENSRE_NO_TELEMETRY", "1")

    analytics = provider.Analytics()

    assert analytics._disabled is True


def test_event_log_path_resolves_under_config_dir(monkeypatch, tmp_path: Path) -> None:
    """The local event log lives next to ``anonymous_id`` and ``analytics_errors.log``.

    Centralizing telemetry artifacts under ``_CONFIG_DIR`` avoids leaking a
    ``posthog_events.txt`` into every shell where the user runs ``opensre``.
    """
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    assert provider._event_log_path() == tmp_path / "posthog_events.txt"


def test_event_log_writes_to_config_dir_not_cwd(monkeypatch, tmp_path: Path) -> None:
    """Regression guard: events must never land in the user's current directory.

    Two sibling tmp dirs make the assertion unambiguous — the one pinned as
    ``_CONFIG_DIR`` should receive the log, the one used as cwd should stay
    empty regardless of where the CLI is invoked from.
    """
    config_dir = tmp_path / "config"
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    monkeypatch.setenv("OPENSRE_ANALYTICS_LOG_EVENTS", "1")
    monkeypatch.setattr(provider, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(provider, "_event_log_state", provider._EventLogState())
    monkeypatch.chdir(cwd)

    provider._log_debug_line("event")

    assert (config_dir / "posthog_events.txt").exists()
    assert not (cwd / "posthog_events.txt").exists()


def test_event_log_creates_config_dir_on_first_write(monkeypatch, tmp_path: Path) -> None:
    """``_CONFIG_DIR`` may not exist on a fresh install — first write must mkdir it."""
    config_dir = tmp_path / "fresh-install" / ".config" / "opensre"
    assert not config_dir.exists()

    monkeypatch.setenv("OPENSRE_ANALYTICS_LOG_EVENTS", "1")
    monkeypatch.setattr(provider, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(provider, "_event_log_state", provider._EventLogState())

    provider._log_debug_line("first line")

    log_path = config_dir / "posthog_events.txt"
    assert log_path.exists()
    assert "first line" in log_path.read_text(encoding="utf-8")


def test_event_log_counter_does_not_drift_when_writes_are_suppressed(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression guard for the rotation-spam bug.

    A naive ``contextlib.suppress(OSError)`` around the write would still
    increment ``lines_written`` on failure, causing a phantom rotation after
    ``_EVENT_LOG_MAX_LINES`` failed attempts. ``_append_log_line`` must bail
    out before the counter touches the cap on a write that didn't succeed.
    """
    monkeypatch.setenv("OPENSRE_ANALYTICS_LOG_EVENTS", "1")
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_EVENT_LOG_MAX_LINES", 3)
    monkeypatch.setattr(provider, "_event_log_state", provider._EventLogState())

    def _raise_oserror(*_args, **_kwargs) -> NoReturn:
        raise OSError("disk write failed")

    monkeypatch.setattr(Path, "open", _raise_oserror)

    for _ in range(10):
        provider._log_debug_line("event")

    assert provider._event_log_state.lines_written == 0
    assert not (tmp_path / "posthog_events.txt.1").exists()


def test_event_log_counter_increments_only_on_successful_write(monkeypatch, tmp_path: Path) -> None:
    """Companion to the suppression test: counter must track real writes 1:1.

    Without this assertion, a future refactor that re-introduces the unsafe
    ``contextlib.suppress`` pattern around the write could silently regress
    the counter-drift fix even with the no-write guard above passing.
    """
    monkeypatch.setenv("OPENSRE_ANALYTICS_LOG_EVENTS", "1")
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_event_log_state", provider._EventLogState())

    for _ in range(7):
        provider._log_debug_line("event")

    assert provider._event_log_state.lines_written == 7
