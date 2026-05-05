from __future__ import annotations

import uuid
from pathlib import Path
from typing import NoReturn

from app.analytics import install, provider
from app.analytics.events import Event


class _StubAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, provider.Properties | None]] = []

    def capture(self, event: Event, properties: provider.Properties | None = None) -> None:
        self.events.append((event, properties))


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


def test_get_or_create_anonymous_id_reuses_persisted_value(monkeypatch, tmp_path: Path) -> None:
    anonymous_id_path = tmp_path / "anonymous_id"

    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)

    first = provider._get_or_create_anonymous_id()
    second = provider._get_or_create_anonymous_id()

    assert first == second
    assert anonymous_id_path.read_text(encoding="utf-8") == first


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

    monkeypatch.setattr(Path, "write_text", _raise_oserror)

    value = provider._get_or_create_anonymous_id()
    assert isinstance(value, str)
    assert value.strip() != ""
    # Verify it's a valid UUID
    uuid.UUID(value)


def test_capture_install_detected_if_needed_returns_false_when_marker_write_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """Test that capture_install_detected_if_needed returns False when marker file write fails."""
    stub = _StubAnalytics()
    marker_path = tmp_path / "installed"
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", marker_path)
    monkeypatch.setattr(provider, "get_analytics", lambda: stub)

    def _raise_oserror(*_args, **_kwargs) -> NoReturn:
        raise OSError("touch failed")

    monkeypatch.setattr(Path, "touch", _raise_oserror)

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


def test_shutdown_analytics_is_noop_when_singleton_not_initialized(monkeypatch) -> None:
    monkeypatch.setattr(provider, "_instance", None)

    provider.shutdown_analytics(flush=False)

    assert provider._instance is None
