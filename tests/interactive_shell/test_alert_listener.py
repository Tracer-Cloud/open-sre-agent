"""Tests for REPL alert listener wiring in :mod:`surfaces.interactive_shell.controller`."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from config.repl_config import ReplConfig
from surfaces.interactive_shell.controller import _alert_listener


def _fake_serve_handle(**_kwargs: object) -> MagicMock:
    handle = MagicMock()
    handle.bound_address = "127.0.0.1:8765"
    return handle


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


class _ReplBodyError(Exception):
    """Stand-in for a real error raised inside the REPL turn body."""


def test_repl_body_error_propagates_unchanged(monkeypatch) -> None:
    """Regression for #3940: an exception from the REPL body while the alert
    listener is active must propagate unchanged, not be masked as
    ``RuntimeError('generator didn't stop after throw()')`` by the listener's
    startup ``except``."""
    handle = _fake_serve_handle()
    monkeypatch.setattr("gateway.web_server.serve_webapp_in_thread", lambda **_k: handle)
    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token="tok")

    with (
        pytest.raises(_ReplBodyError, match="real repl error"),
        _alert_listener(cfg, Console(force_terminal=False)) as inbox,
    ):
        assert inbox is not None
        raise _ReplBodyError("real repl error")

    # Cleanup still runs on the body-error path (listener is torn down).
    handle.stop.assert_called_once()


def test_startup_failure_yields_none_without_masking(monkeypatch) -> None:
    """A genuine listener bring-up failure still degrades gracefully: the
    context manager yields ``None`` and does not raise."""

    def _boom(**_kwargs: object) -> MagicMock:
        raise OSError("port already in use")

    monkeypatch.setattr("gateway.web_server.serve_webapp_in_thread", _boom)
    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token="tok")

    with _alert_listener(cfg, Console(force_terminal=False)) as inbox:
        assert inbox is None
