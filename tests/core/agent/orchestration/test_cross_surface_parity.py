"""Cross-surface parity: every client routes and replies identically.

Surfaces under test:

* ``shell`` — ``execute_shell_turn`` (interactive REPL / CLI one-shot)
* ``headless`` — ``HeadlessAgent.dispatch``
* ``gateway_handler`` — ``GatewayTurnHandler`` (Telegram/API gateway)

Each test wires ONE tool registry and ONE pair of LLMs, drives the same message
through all three entry points, and asserts identical routing + response shape.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

import surfaces.interactive_shell.runtime.slash_adapter as slash_adapter
from core.agent_harness.tools.action_tools import get_action_tool
from gateway.core.runtime.turn_handler import GatewayTurnHandler
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

        monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)
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
    handler = GatewayTurnHandler(console=console())
    handler("run probe", session, sink, logging.getLogger("test.parity.gateway.outbound"))

    assert sink.finalized is not None
    assert sink.streamed == []
    assert "probe executed" in sink.finalized
    # ``answered`` is False here and the turn still succeeded: keying the
    # failure flag off it would mark the majority of tool turns as failures.
    assert sink.finalized_failed is False


def test_gateway_handler_streams_answer_on_assistant_turn(
    parity_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway session (investigation = ()) on gather path gets deferred paint flushed exactly once."""
    configure, _ = parity_env
    configure(tools=[probe_tool()], action_mode="text")

    snapshot, sink = run_gateway_turn_with_sink("why opensre", monkeypatch)

    assert snapshot.answered is True
    assert PARITY_ANSWER in snapshot.assistant_text
    # Deferred paint flushed exactly once, handler still does not double-finalize
    assert len(sink.finished) == 1
    assert PARITY_ANSWER in sink.finished[0]
    assert sink.finalized is None
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


def test_gather_path_non_deferred_stream_does_not_flush(
    parity_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T4: The non-deferred path must NOT flush to prevent duplicate-post regression."""
    configure, _ = parity_env
    from core.agent_harness.turns.turn_results import ToolCallingTurnResult

    # Create a handoff that sets handoff_requires_gather=False -> defer=False
    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        _ = text
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="action handled",
            handoff_contents=("diagnostic:test",),
            handoff_requires_gather=False,  # This forces defer=False
        )

    from core.agent_harness.turns.headless_adapters import InMemorySessionStore, NoopTurnAccounting
    from core.agent_harness.turns.orchestrator import run_turn

    session = InMemorySessionStore()
    finish_calls: list[str] = []

    class FakeRun:
        response_text = PARITY_ANSWER

    def fake_answer(*_args: object, **_kwargs: object) -> FakeRun:
        return FakeRun()

    class FakeOutput:
        def finish_streamed_response(self, text: str) -> None:
            finish_calls.append(text)

    # handoff_requires_gather=False -> defer=False, finish_streamed_response never called
    result = run_turn(
        "test message",
        session,
        execute_actions=execute_actions,
        answer=fake_answer,
        gather=lambda *_a, **_k: None,
        accounting=NoopTurnAccounting(),
        output=FakeOutput(),  # type: ignore[arg-type]
    )

    assert result.final_intent == "cli_agent_fallback"
    assert len(finish_calls) == 0  # No flush on non-deferred path


def test_failed_answer_stream_does_not_clobber_error(
    parity_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T5: A failed answer stream must not have its error clobbered by finish flush."""
    from core.agent_harness.turns.headless_adapters import InMemorySessionStore, NoopTurnAccounting
    from core.agent_harness.turns.orchestrator import run_turn
    from core.agent_harness.turns.turn_results import ToolCallingTurnResult

    configure, _ = parity_env
    configure(tools=[probe_tool()], action_mode="text")

    session = InMemorySessionStore()
    finish_calls: list[str] = []

    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        _ = text
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=False,
            response_text="",
            handoff_contents=("diagnostic:test",),
        )

    def fake_answer(*_args: object, **_kwargs: object) -> None:
        # answer returns None after the sink rendered an error -> no flush
        return None

    class FakeOutput:
        def finish_streamed_response(self, text: str) -> None:
            finish_calls.append(text)

    run_turn(
        "test error case",
        session,
        execute_actions=execute_actions,
        answer=fake_answer,
        gather=lambda *_a, **_k: "gathered evidence",
        accounting=NoopTurnAccounting(),
        output=FakeOutput(),  # type: ignore[arg-type]
    )

    # Failed answer should not flush (run is None)
    assert len(finish_calls) == 0


def test_confirms_pending_offer_still_flushes_interactive_shell_half(
    parity_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6: The confirms_pending leg: armed offer + 'yes' + action does not handle -> gather path still flushes."""
    from core.agent_harness.session.pending_offer import PendingInvestigationOffer
    from core.agent_harness.turns.headless_adapters import InMemorySessionStore, NoopTurnAccounting
    from core.agent_harness.turns.orchestrator import run_turn
    from core.agent_harness.turns.turn_results import ToolCallingTurnResult

    configure, _ = parity_env
    configure(tools=[probe_tool()], action_mode="text")

    session = InMemorySessionStore()
    # Arm a pending investigation offer
    session.pending_investigation_offer = PendingInvestigationOffer(alert_text="test alert")
    finish_calls: list[str] = []

    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        _ = text
        # Action does not handle -> gather path
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=False,
            response_text="",
            handoff_contents=(),
        )

    class FakeRun:
        response_text = PARITY_ANSWER

    def fake_answer(*_args: object, **_kwargs: object) -> FakeRun:
        return FakeRun()

    class FakeOutput:
        def finish_streamed_response(self, text: str) -> None:
            finish_calls.append(text)

    # confirms_pending + gather path still flushes (interactive-shell half)
    result = run_turn(
        "yes",  # This should expand to the pending offer and then go to gather
        session,
        execute_actions=execute_actions,
        answer=fake_answer,
        gather=lambda *_a, **_k: "gathered evidence",
        accounting=NoopTurnAccounting(),
        output=FakeOutput(),  # type: ignore[arg-type]
    )

    assert result.final_intent == "cli_agent_fallback"
    # Should still flush even with confirms_pending
    assert len(finish_calls) == 1


def test_deferred_flush_paints_the_canonical_closer_rewrite(
    parity_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flush must run *after* the offer rewrite, and survive ``replace()``.

    Two independent requirements meet on this path, and neither was pinned:

    * ``_RouteOutcome`` is rebuilt with ``dataclasses.replace`` inside the
      capability guard. If ``paint_deferred`` were dropped there, a surface
      that held the paint would never be flushed and the tail would be lost --
      the same production symptom, one branch over.
    * The flush is placed after the guard closes so it paints what
      ``finalize_gather_investigation_offer`` rewrote. Flushing before the
      guard would deliver the model's raw closer while the session armed the
      canonical one, so bare ``yes`` would accept an offer the user never saw.
    """
    from core.agent_harness.turns.headless_adapters import InMemorySessionStore, NoopTurnAccounting
    from core.agent_harness.turns.orchestrator import run_turn
    from core.agent_harness.turns.turn_results import ToolCallingTurnResult

    configure, _ = parity_env
    configure(tools=[probe_tool()], action_mode="text")

    raw_answer = "The crashloop is off pod worker-5cc.\n\nWant me to: check the deploy history?"
    canonical = "The crashloop is off pod worker-5cc.\n\n**Want me to:** run a full investigation"

    session = InMemorySessionStore()
    finish_calls: list[str] = []

    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        _ = text
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=False,
            response_text="",
            handoff_contents=(),
        )

    class FakeRun:
        response_text = raw_answer

    def fake_answer(*_args: object, **_kwargs: object) -> FakeRun:
        return FakeRun()

    class FakeOutput:
        def finish_streamed_response(self, text: str) -> None:
            finish_calls.append(text)

    run_turn(
        "why is checkout failing",
        session,
        execute_actions=execute_actions,
        answer=fake_answer,
        gather=lambda *_a, **_k: "gathered evidence",
        accounting=NoopTurnAccounting(),
        output=FakeOutput(),  # type: ignore[arg-type]
    )

    # The guard rewrote the closer, so the offer is armed...
    assert session.pending_investigation_offer is not None
    # ...and the surface was painted exactly once, with the rewritten text.
    assert finish_calls == [canonical]
