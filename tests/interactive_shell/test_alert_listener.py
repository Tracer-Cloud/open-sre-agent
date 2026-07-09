"""Tests for REPL alert listener wiring in :mod:`surfaces.interactive_shell.controller`."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from config.repl_config import ReplConfig
from surfaces.interactive_shell.controller import _alert_listener


def test_alert_listener_replaces_stale_process_token(monkeypatch) -> None:
    os.environ["OPENSRE_ALERT_LISTENER_TOKEN"] = "stale"
    captured: list[str | None] = []

    def _fake_serve(**_kwargs: object) -> MagicMock:
        captured.append(os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN"))
        handle = MagicMock()
        handle.bound_address = "127.0.0.1:8765"
        return handle

    monkeypatch.setattr(
        "gateway.web_server.serve_webapp_in_thread",
        _fake_serve,
    )
    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token="fresh")

    with _alert_listener(cfg, Console(force_terminal=False)) as inbox:
        assert inbox is not None
        assert captured == ["fresh"]

    assert os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN") == "stale"


def test_alert_listener_clears_token_when_unconfigured(monkeypatch) -> None:
    os.environ["OPENSRE_ALERT_LISTENER_TOKEN"] = "stale"
    captured: list[str | None] = []

    def _fake_serve(**_kwargs: object) -> MagicMock:
        captured.append(os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN"))
        handle = MagicMock()
        handle.bound_address = "127.0.0.1:8765"
        return handle

    monkeypatch.setattr(
        "gateway.web_server.serve_webapp_in_thread",
        _fake_serve,
    )
    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token=None)

    with _alert_listener(cfg, Console(force_terminal=False)) as inbox:
        assert inbox is not None
        assert captured == [None]

    assert os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN") == "stale"


def test_alert_listener_propagates_body_exception_without_masking(monkeypatch) -> None:
    """A failure in the REPL body (inside the `with`) must propagate unchanged,
    not be swallowed and re-yielded as a "could not start" failure (which makes
    @contextmanager raise "generator didn't stop after throw()")."""
    os.environ.pop("OPENSRE_ALERT_LISTENER_TOKEN", None)
    handle = MagicMock()
    handle.bound_address = "127.0.0.1:8765"

    def _fake_serve(**_kwargs: object) -> MagicMock:
        return handle

    monkeypatch.setattr("gateway.web_server.serve_webapp_in_thread", _fake_serve)
    from core.domain.alerts import inbox as _alert_inbox

    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token="tok")

    class _BodyError(RuntimeError):
        pass

    with (
        pytest.raises(_BodyError, match="boom"),
        _alert_listener(cfg, Console(force_terminal=False)) as inbox,
    ):
        assert inbox is not None
        raise _BodyError("boom")

    # cleanup still ran: server stopped, inbox cleared, token restored
    handle.stop.assert_called_once()
    assert _alert_inbox.get_current_inbox() is None
    assert os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN") is None


def test_alert_listener_degrades_to_none_when_startup_fails(monkeypatch) -> None:
    """A startup failure yields None and does not crash the REPL, and restores
    the token env var."""
    os.environ.pop("OPENSRE_ALERT_LISTENER_TOKEN", None)

    def _boom_serve(**_kwargs: object) -> MagicMock:
        raise OSError("port in use")

    monkeypatch.setattr("gateway.web_server.serve_webapp_in_thread", _boom_serve)
    from core.domain.alerts import inbox as _alert_inbox

    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token="tok")

    with _alert_listener(cfg, Console(force_terminal=False)) as inbox:
        assert inbox is None

    assert _alert_inbox.get_current_inbox() is None
    assert os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN") is None
