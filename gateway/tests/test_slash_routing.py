"""Gateway slash-command routing for Telegram and other headless surfaces."""

from __future__ import annotations

import io
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStorage
from core.agent_harness.tools.action_tools import get_action_tool
from gateway.turn_handler import GatewayTurnHandler
from tests.core.agent.orchestration.cross_surface_parity_harness import RecordingGatewaySink


def _gateway_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False, width=100)


def _run_gateway_slash(message: str) -> RecordingGatewaySink:
    session = SessionCore(storage=InMemorySessionStorage())
    sink = RecordingGatewaySink()
    handler = GatewayTurnHandler(console=_gateway_console())
    handler(message, session, sink, logging.getLogger("test.gateway.slash"))
    return sink


def test_gateway_registers_slash_invoke_tool() -> None:
    """Harness adapters wired at gateway boot must expose slash_invoke to action turns."""
    slash = get_action_tool("slash_invoke")
    assert slash is not None
    assert slash.name == "slash_invoke"


def test_gateway_status_slash_is_not_swallowed() -> None:
    """Literal /status must route through slash_invoke and return session diagnostics."""
    sink = _run_gateway_slash("/status")
    assert sink.finalized is not None
    assert "I didn't have anything to add for that." not in sink.finalized
    assert "interactions" in sink.finalized.lower()


def test_gateway_investigate_slash_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Literal /investigate <template> must run the investigation slash handler."""

    def _fake_run_sample_alert_for_session(**_kwargs: object) -> dict[str, object]:
        return {"status": "completed", "summary": "parity investigation ok"}

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.investigation_adapter.run_sample_alert_for_session",
        _fake_run_sample_alert_for_session,
    )

    sink = _run_gateway_slash("/investigate generic")
    assert sink.finalized is not None
    assert "I didn't have anything to add for that." not in sink.finalized
    assert "generic" in sink.finalized.lower()


def test_gateway_onboard_slash_returns_headless_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Literal /onboard on SessionCore must not spawn a blocking interactive wizard."""
    recorded: list[list[str]] = []

    def _fake_run_cli_command(*_args: object, **_kwargs: object) -> bool:
        recorded.append(["onboard"])
        return True

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.cli_parity.run_cli_command",
        _fake_run_cli_command,
    )

    sink = _run_gateway_slash("/onboard")
    assert recorded == []
    assert sink.finalized is not None
    assert "interactive wizard" in sink.finalized.lower()
    assert "uv run opensre onboard" in sink.finalized


def test_gateway_integrations_setup_returns_headless_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Literal /integrations setup must not spawn a blocking credential wizard on gateway."""
    recorded: list[list[str]] = []

    def _fake_run_cli_command(
        _console: Any,
        args: list[str],
        **_kwargs: object,
    ) -> bool:
        recorded.append(list(args))
        return True

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.integrations.run_cli_command",
        _fake_run_cli_command,
    )

    sink = _run_gateway_slash("/integrations setup grafana")
    assert recorded == []
    assert sink.finalized is not None
    assert "grafana" in sink.finalized.lower()
    assert "succeeded" not in sink.finalized.lower()
    assert "timed out" not in sink.finalized.lower()
    assert "uv run opensre integrations setup grafana" in sink.finalized
    assert "Launching" not in (sink.finalized or "")


def test_gateway_integrations_setup_returns_headless_guidance_even_with_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway SessionCore returns headless guidance even when stdin is a TTY (e.g. tmux)."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.components.choice_menu.repl_tty_interactive",
        lambda: True,
    )
    recorded: list[list[str]] = []

    def _fake_run_cli_command(
        _console: Any,
        args: list[str],
        **_kwargs: object,
    ) -> bool:
        recorded.append(list(args))
        return True

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.integrations.run_cli_command",
        _fake_run_cli_command,
    )

    sink = _run_gateway_slash("/integrations setup grafana")
    assert recorded == []
    assert sink.finalized is not None
    assert "Launching" not in (sink.finalized or "")


def test_gateway_manager_registers_harness_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway boot must register harness adapters so production turns see slash_invoke."""
    calls: list[str] = []

    def _register_integrations() -> None:
        calls.append("integrations")

    def _register_tools() -> None:
        calls.append("tools")

    monkeypatch.setattr(
        "integrations.harness_adapters.register_harness_adapters",
        _register_integrations,
    )
    monkeypatch.setattr("tools.harness_adapters.register_harness_adapters", _register_tools)
    monkeypatch.setattr(
        "gateway.manager.start_telegram_worker",
        lambda **_kwargs: (MagicMock(), MagicMock()),
    )

    from gateway.manager import GatewayManager

    GatewayManager().start_gateway(wait=False)
    assert calls == ["integrations", "tools"]
