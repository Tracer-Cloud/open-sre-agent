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
from gateway.core.runtime.turn_handler import GatewayTurnHandler
from tests.core.agent.orchestration.cross_surface_parity_harness import (
    RecordingGatewaySink,
    headless_slash_ports,
)


def _gateway_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False, width=100)


def _run_gateway_slash(message: str) -> RecordingGatewaySink:
    session = SessionCore(storage=InMemorySessionStorage())
    sink = RecordingGatewaySink()
    handler = GatewayTurnHandler(
        console=_gateway_console(),
        slash_ports_factory=headless_slash_ports,
    )
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


def test_gateway_investigate_discord_alert_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discord maps slash alert text to /investigate alert:<text>."""

    def _fake_run_investigation_for_session(**_kwargs: object) -> dict[str, object]:
        return {"status": "completed", "summary": "discord alert ok"}

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.investigation_adapter.run_investigation_for_session",
        _fake_run_investigation_for_session,
    )

    sink = _run_gateway_slash("/investigate alert:High error rate on checkout")
    assert sink.finalized is not None
    assert "failed" not in (sink.finalized or "").lower()


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
    from bootstrap.process import reset_process_runtime_for_tests

    calls: list[str] = []

    def _record(name: str):
        def _step() -> None:
            calls.append(name)

        return _step

    # Boot runs once per profile; clear it so this test sees the real sequence.
    reset_process_runtime_for_tests()
    # Registration happens in bootstrap.process via GATEWAY_PROFILE.
    monkeypatch.setattr("bootstrap.process.install_harness_adapters", _record("adapters"))
    monkeypatch.setattr("bootstrap.process.install_scheduler_runners", _record("runners"))
    monkeypatch.setattr(
        "platform.observability.errors.sentry.init_sentry",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "core.llm.internal.preload.preload_llm_clients",
        lambda: None,
    )
    monkeypatch.setattr(
        "platform.sandbox.capabilities.boot_capability_warnings",
        lambda: [],
    )
    monkeypatch.setattr(
        "gateway.core.runtime.manager.start_telegram_worker",
        lambda **_kwargs: (MagicMock(), MagicMock()),
    )
    # Keep this test focused on adapter registration (life-cycle tests cover scheduler).
    monkeypatch.setattr(
        "gateway.core.runtime.manager.GatewayManager._start_scheduler",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "gateway.core.runtime.manager.GatewayManager._start_web",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "gateway.core.runtime.manager.GatewayManager._publish_status",
        lambda *_args, **_kwargs: None,
    )

    from gateway.core.runtime.manager import GatewayManager

    GatewayManager().start_gateway(wait=False)

    # GATEWAY_PROFILE registers adapters at boot; scheduler runners come later,
    # when the scheduler stage starts.
    assert "adapters" in calls
    assert "runners" not in calls
    reset_process_runtime_for_tests()
