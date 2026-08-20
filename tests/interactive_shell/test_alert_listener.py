"""Tests for REPL alert listener wiring in :mod:`surfaces.interactive_shell.controller`."""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

from rich.console import Console

from config.platform_bootstrap import ensure_project_platform_package
from config.repl_config import ReplConfig
from surfaces.interactive_shell.controller import _alert_listener


def test_alert_listener_replaces_stale_process_token(monkeypatch) -> None:
    os.environ["OPENSRE_ALERT_LISTENER_TOKEN"] = "stale"
    captured: list[str | None] = []

    def _fake_serve(*_args: object, **_kwargs: object) -> MagicMock:
        captured.append(os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN"))
        handle = MagicMock()
        handle.bound_address = "127.0.0.1:8765"
        return handle

    monkeypatch.setattr(
        "platform.asgi_server.serve_asgi_in_thread",
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

    def _fake_serve(*_args: object, **_kwargs: object) -> MagicMock:
        captured.append(os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN"))
        handle = MagicMock()
        handle.bound_address = "127.0.0.1:8765"
        return handle

    monkeypatch.setattr(
        "platform.asgi_server.serve_asgi_in_thread",
        _fake_serve,
    )
    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token=None)

    with _alert_listener(cfg, Console(force_terminal=False)) as inbox:
        assert inbox is not None
        assert captured == [None]

    assert os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN") == "stale"


def test_alert_listener_bootstraps_when_stdlib_platform_is_cached(monkeypatch) -> None:
    """Embedding hosts may cache stdlib ``platform`` before the shell starts.

    Without ``ensure_project_platform_package``, ``from platform.alert_intake``
    fails and the listener exception path leaves the shell with no intake.
    """
    ensure_project_platform_package()

    def _fake_serve(*_args: object, **_kwargs: object) -> MagicMock:
        handle = MagicMock()
        handle.bound_address = "127.0.0.1:8765"
        return handle

    monkeypatch.setattr("platform.asgi_server.serve_asgi_in_thread", _fake_serve)
    monkeypatch.setattr("platform.alert_intake.build_alert_intake_app", object)

    stub = types.ModuleType("platform")
    monkeypatch.setitem(sys.modules, "platform", stub)
    assert not hasattr(sys.modules["platform"], "__path__")

    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token="fresh")

    with _alert_listener(cfg, Console(force_terminal=False)) as inbox:
        assert inbox is not None
        assert hasattr(sys.modules["platform"], "__path__")


def test_controller_constructs_when_stdlib_platform_is_cached(monkeypatch) -> None:
    """``InteractiveShellController`` must bootstrap before importing turn_host.

    Embedding hosts that cache stdlib ``platform`` after the controller module
    loaded would otherwise fail construction with ModuleNotFoundError on
    ``platform.turn_host``.
    """
    ensure_project_platform_package()
    from surfaces.interactive_shell.controller import InteractiveShellController
    from surfaces.interactive_shell.session import Session

    stub = types.ModuleType("platform")
    monkeypatch.setitem(sys.modules, "platform", stub)
    for name in list(sys.modules):
        if name.startswith("platform."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    assert not hasattr(sys.modules["platform"], "__path__")

    controller = InteractiveShellController(
        Session(),
        console=Console(force_terminal=False),
    )
    assert controller.turn_runtime.turn_handler is not None
    assert hasattr(sys.modules["platform"], "__path__")
