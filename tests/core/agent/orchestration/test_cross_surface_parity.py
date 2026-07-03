"""Cross-surface parity: every client routes and replies identically.

Surfaces under test:

* ``shell`` — ``execute_shell_turn`` (interactive REPL / CLI one-shot)
* ``headless`` — ``dispatch_message_to_headless_agent``
* ``agent_static`` — ``Agent.dispatch_message_to_headless_agent``
* ``gateway_handler`` — ``build_gateway_turn_handler`` (Telegram/API gateway)

Each test wires ONE tool registry and ONE pair of LLMs, drives the same message
through all four entry points, and asserts identical routing + response shape.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

import tools.interactive_shell.actions.slash as slash_tool
from core.agent_harness.tools.action_tools import get_action_tool
from gateway.turn_handler import build_gateway_turn_handler
from tests.core.agent.orchestration.cross_surface_parity_harness import (
    ALL_SURFACES,
    PARITY_ANSWER,
    RecordingGatewaySink,
    assert_surfaces_match,
    collect_all_surfaces,
    console,
    fresh_session,
    integration_gated_tool,
    integrations_seen,
    probe_run_count,
    probe_tool,
    run_gateway_turn_with_sink,
    run_surface,
    shell_run_tool,
    wire_llms,
    wire_tool_registry,
)
from tools.registry import clear_tool_registry_cache

SLACK_INTEGRATIONS = {"slack": {"webhook_url": "https://hooks.example/test"}}


@pytest.fixture
def parity_env(monkeypatch: pytest.MonkeyPatch):
    """Register the parity probe tool and expose LLM mode setters."""

    def _configure(
        *,
        tools: list[Any],
        action_mode: str,
        action_tool_name: str = "parity_probe",
    ) -> None:
        wire_tool_registry(monkeypatch, tools)
        wire_llms(monkeypatch, action_mode=action_mode, action_tool_name=action_tool_name)

    def _configure_with_slash(*, action_mode: str = "text") -> list[str]:
        clear_tool_registry_cache()  # fresh registry (project test convention)
        slash = get_action_tool("slash_invoke")
        assert slash is not None
        dispatched: list[str] = []

        def _fake_dispatch(command: str, session: Any, console: Any, **_kwargs: object) -> bool:
            _ = (session, console)
            dispatched.append(command)
            return True

        monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
        _configure(tools=[slash, probe_tool()], action_mode=action_mode)
        return dispatched

    return _configure, _configure_with_slash


def test_all_surfaces_execute_action_tool(parity_env, monkeypatch: pytest.MonkeyPatch) -> None:
    configure, _ = parity_env
    configure(tools=[probe_tool()], action_mode="tool")

    snapshots = collect_all_surfaces("run the parity probe", monkeypatch)
    assert_surfaces_match(snapshots)

    for snap in snapshots.values():
        assert snap.probe_ran is True
        assert snap.action_handled is True
        assert snap.final_intent == "cli_agent_handled"
        assert snap.answered is False


def test_all_surfaces_answer_questions_via_assistant(
    parity_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure, _ = parity_env
    configure(tools=[probe_tool()], action_mode="text")

    snapshots = collect_all_surfaces("what is the meaning of opensre", monkeypatch)
    assert_surfaces_match(snapshots)

    for snap in snapshots.values():
        assert snap.probe_ran is False
        assert snap.action_handled is False
        assert snap.final_intent == "cli_agent_fallback"
        assert snap.answered is True
        assert PARITY_ANSWER in snap.assistant_text


def test_all_surfaces_literal_slash_uses_action_agent(
    parity_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, configure_with_slash = parity_env
    dispatched = configure_with_slash(action_mode="text")

    snapshots = collect_all_surfaces("/status", monkeypatch)
    assert_surfaces_match(snapshots)

    assert all(item == "/status" for item in dispatched)
    assert len(dispatched) == len(ALL_SURFACES)
    for snap in snapshots.values():
        assert snap.action_handled is True
        assert snap.final_intent == "cli_agent_handled"
        assert snap.answered is False


def test_all_surfaces_bang_shell_uses_action_agent(
    parity_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure, _ = parity_env
    configure(tools=[shell_run_tool()], action_mode="text")

    snapshots = collect_all_surfaces("!echo parity", monkeypatch)
    assert_surfaces_match(snapshots)

    assert probe_run_count() == len(ALL_SURFACES)
    for snap in snapshots.values():
        assert snap.action_handled is True
        assert snap.final_intent == "cli_agent_handled"
        assert snap.answered is False


def test_all_surfaces_pass_session_integrations_to_tool_resolution(
    parity_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each surface must resolve action tools from the live session integrations."""
    configure, _ = parity_env
    configure(tools=[integration_gated_tool(), probe_tool()], action_mode="text")

    for surface in ALL_SURFACES:
        seen_before = len(integrations_seen())
        run_surface(surface, "hello", monkeypatch, integrations=SLACK_INTEGRATIONS)
        recorded = integrations_seen()[seen_before:]
        assert recorded, f"{surface!r} never resolved tools from session integrations"
        assert all(item == SLACK_INTEGRATIONS for item in recorded), (
            f"{surface!r} passed unexpected integrations to tool resolution: {recorded}"
        )


def test_all_surfaces_execute_integration_gated_tool(
    parity_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration-scoped tools must be available on every surface when configured."""
    configure, _ = parity_env
    slack_tool = integration_gated_tool()
    configure(
        tools=[slack_tool, probe_tool()],
        action_mode="tool",
        action_tool_name=slack_tool.name,
    )

    snapshots = collect_all_surfaces(
        "send slack update", monkeypatch, integrations=SLACK_INTEGRATIONS
    )
    assert_surfaces_match(snapshots)

    for snap in snapshots.values():
        assert snap.probe_ran is True
        assert snap.action_handled is True
        assert snap.final_intent == "cli_agent_handled"
        assert snap.answered is False
        assert "slack probe executed" in snap.assistant_text


def test_gateway_handler_outbound_finalize_on_action_only_turn(
    parity_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway handler must finalize action-only replies (answered=False path)."""
    configure, _ = parity_env
    configure(tools=[probe_tool()], action_mode="tool")

    session = fresh_session()
    sink = RecordingGatewaySink()
    handler = build_gateway_turn_handler(console=console())
    handler("run probe", session, sink, logging.getLogger("test.parity.gateway.outbound"))

    assert sink.finalized is not None
    assert sink.streamed == []
    assert "probe executed" in sink.finalized


def test_gateway_handler_streams_answer_on_assistant_turn(
    parity_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Answer turns stream via the sink; they must not call finalize."""
    configure, _ = parity_env
    configure(tools=[probe_tool()], action_mode="text")

    snapshot, sink = run_gateway_turn_with_sink("why opensre", monkeypatch)

    assert snapshot.answered is True
    assert PARITY_ANSWER in snapshot.assistant_text
    assert sink.finalized is None
    assert sink.streamed
    assert PARITY_ANSWER in sink.streamed[-1]
    assert PARITY_ANSWER in sink.outbound_text


def test_turn_snapshot_fields_action_vs_answer(parity_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Document expected routing facts for action vs answer paths on one surface."""
    configure, _ = parity_env
    configure(tools=[probe_tool()], action_mode="tool")
    action = run_surface("headless", "run probe", monkeypatch)

    configure(tools=[probe_tool()], action_mode="text")
    answer = run_surface("headless", "why", monkeypatch)

    assert action.final_intent == "cli_agent_handled"
    assert action.action_handled is True
    assert action.action_planned == 1
    assert action.answered is False
    assert action.probe_ran is True
    assert "probe executed" in action.assistant_text

    assert answer.final_intent == "cli_agent_fallback"
    assert answer.action_handled is False
    assert answer.action_planned == 0
    assert answer.answered is True
    assert answer.assistant_text == PARITY_ANSWER
    assert answer.probe_ran is False
