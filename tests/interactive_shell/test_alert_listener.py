"""Tests for REPL alert listener wiring in :mod:`surfaces.interactive_shell.controller`."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

from rich.console import Console

from config.repl_config import ReplConfig
from surfaces.interactive_shell.controller import _alert_listener

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_in_fresh_interpreter(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(_REPO_ROOT),
    )


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


def test_alert_listener_bootstraps_when_stdlib_platform_is_cached() -> None:
    """Embedding hosts may cache stdlib ``platform`` before the shell starts."""
    result = _run_in_fresh_interpreter(
        """
        from config.platform_bootstrap import ensure_project_platform_package
        from config.repl_config import ReplConfig
        from rich.console import Console
        from unittest.mock import MagicMock
        import sys, types
        ensure_project_platform_package()
        from surfaces.interactive_shell.controller import _alert_listener
        def _fake_serve(*_a, **_k):
            h = MagicMock()
            h.bound_address = "127.0.0.1:8765"
            return h
        import platform.asgi_server as asgi
        import platform.alert_intake as intake
        asgi.serve_asgi_in_thread = _fake_serve
        intake.build_alert_intake_app = object
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token="fresh")
        with _alert_listener(cfg, Console(force_terminal=False)) as inbox:
            assert inbox is not None
            assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_controller_constructs_when_stdlib_platform_is_cached() -> None:
    result = _run_in_fresh_interpreter(
        """
        from config.platform_bootstrap import ensure_project_platform_package
        from rich.console import Console
        import sys, types
        ensure_project_platform_package()
        from surfaces.interactive_shell.controller import InteractiveShellController
        from surfaces.interactive_shell.session import Session
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        controller = InteractiveShellController(Session(), console=Console(force_terminal=False))
        assert controller.turn_runtime.turn_handler is not None
        assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_create_repl_runtime_context_bootstraps_when_stdlib_platform_cached() -> None:
    result = _run_in_fresh_interpreter(
        """
        from config.platform_bootstrap import ensure_project_platform_package
        import sys, types
        ensure_project_platform_package()
        from surfaces.interactive_shell.runtime.context import create_repl_runtime_context
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        ctx = create_repl_runtime_context()
        assert ctx.session is not None
        assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
