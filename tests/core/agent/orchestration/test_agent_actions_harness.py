"""Action-execution tests over model tool calls, not planner DTOs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest
from rich.console import Console

import tools.interactive_shell.actions.slash as slash_tool
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.turns.action_driver import (
    ActionTurnPlan,
    ToolCallingDeps,
    _build_action_agent,
    _turn_resolved_integrations,
    run_action_agent_turn,
)
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.tool_framework.registered_tool import RegisteredTool
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


class _GenericActionToolProvider:
    def __init__(self, tool: RegisteredTool) -> None:
        self._tool = tool

    def action_tools(self, **_kwargs: object) -> list[RegisteredTool]:
        return [self._tool]

    def observer(self, **_kwargs: object):
        return lambda _kind, _data: None


class _OutputSink:
    def __init__(self, console: Console) -> None:
        self._console = console

    def print(self, message: str = "") -> None:
        self._console.print(message)

    def render_response_header(self, label: str) -> None:
        self._console.print(label)

    def render_error(self, message: str) -> None:
        self._console.print(message)

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        text = "".join(chunks)
        self._console.print(text)
        return text


def test_execute_with_harness_runs_slash_tool_call(monkeypatch) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    harness = ActionExecutionHarness(
        llm=FakeActionLLM([tool_response("slash_invoke", {"command": "/health", "args": []})])
    )
    session = Session()

    result = run_action_tool_turn(
        "check health",
        session,
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 1
    assert dispatched == ["/health"]
    assert "slash_invoke" in harness.llm.tool_schema_names


def test_generic_registered_action_tool_result_marks_turn_handled() -> None:
    tool = RegisteredTool(
        name="fake_send_message",
        description="Send a fake message.",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        source="knowledge",
        surfaces=("action",),
        run=lambda message: {"status": "sent", "message": message},
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM([tool_response("fake_send_message", {"message": "hello"})])
    )

    result = run_action_agent_turn(
        "send a fake message",
        Session(),
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
        is_tty=False,
    )

    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 1
    assert result.executed_success_count == 1
    assert 'fake_send_message input: {"message": "hello"}' in result.response_text
    assert '"status": "sent"' in result.response_text
    assert "fake_send_message" in harness.llm.tool_schema_names


def test_literal_slash_command_dispatches_deterministically_without_llm(
    monkeypatch,
) -> None:
    """A literal ``/command`` typed by the user dispatches via ``slash_invoke``
    without consulting the action-agent LLM, so slash commands keep working when
    the LLM is unavailable (e.g. a provider with no credit)."""
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))
    session = Session()

    result = run_action_tool_turn(
        "/sessions",
        session,
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert result.planned_count == 1
    assert dispatched == ["/sessions"]
    assert session.history == [{"type": "slash", "text": "/sessions", "ok": True}]
    # The deterministic path must not consult the action-agent LLM.
    assert harness.llm.invocations == 0


def test_literal_slash_command_forwards_args_without_llm(monkeypatch) -> None:
    """``/login chatgpt`` dispatches with its positional args and no LLM call."""
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))

    result = run_action_tool_turn(
        "/login chatgpt",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert dispatched == ["/login chatgpt"]
    assert harness.llm.invocations == 0


def test_natural_language_still_routes_through_action_agent(monkeypatch) -> None:
    """Non-slash, free-form text is still selected by the action-agent LLM —
    the deterministic path is limited to literal ``/command`` input."""

    def _unexpected_dispatch(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("free-form text must not deterministically dispatch a slash command")

    monkeypatch.setattr(slash_tool, "dispatch_slash", _unexpected_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))

    result = run_action_tool_turn(
        "log me in please",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert harness.llm.invocations == 1
    assert result.handled is False


def test_execute_with_harness_hands_off_handoff_only_tool_call() -> None:
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [tool_response("assistant_handoff", {"content": "docs:help"})],
        )
    )

    result = run_action_tool_turn(
        "half actionable prompt",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is False
    assert result.has_unhandled_clause is False
    assert result.planned_count == 0
    assert result.handoff_contents == ("docs:help",)
    assert "Requested actions" not in harness.console_buffer.getvalue()


def test_local_llama_handoff_populates_handoff_contents() -> None:
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [tool_response("assistant_handoff", {"content": "provider:local_llama_connect"})],
        )
    )

    result = run_action_tool_turn(
        "please connect to local llama",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is False
    assert result.handoff_contents == ("provider:local_llama_connect",)


def test_route_handoff_skips_handled_without_llm() -> None:
    from core.agent_harness.turns.orchestrator import TurnRoutingInput, _route_turn

    routing = TurnRoutingInput(
        action_handled=True,
        executed_success_count=1,
        has_observation=False,
    )
    route = _route_turn(routing, handoff_contents=("provider:local_llama_connect",))
    assert route.intent == "gather_and_answer"


def test_route_handled_without_handoff_stays_action_only() -> None:
    from core.agent_harness.turns.orchestrator import TurnRoutingInput, _route_turn

    routing = TurnRoutingInput(
        action_handled=True,
        executed_success_count=1,
        has_observation=False,
    )
    route = _route_turn(routing)
    assert route.intent == "handled_without_llm"


def test_route_investigation_dispatch_skips_gather_even_with_handoff() -> None:
    from core.agent_harness.turns.orchestrator import TurnRoutingInput, _route_turn

    routing = TurnRoutingInput(
        action_handled=True,
        executed_success_count=1,
        has_observation=False,
        investigation_dispatched=True,
    )
    route = _route_turn(
        routing,
        handoff_contents=("chat:non_actionable_literal",),
    )
    assert route.intent == "handled_without_llm"


def test_run_turn_passes_handoff_contents_to_assistant() -> None:
    captured: list[tuple[str, ...]] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
            handoff_contents=("provider:local_llama_connect",),
        )

    def _answer(*_args: Any, handoff_contents: tuple[str, ...] = (), **_kwargs: Any) -> None:
        captured.append(handoff_contents)
        return None

    run_turn(
        "please connect to local llama",
        Session(),
        execute_actions=_execute,
        gather=lambda *_args, **_kwargs: None,
        answer=_answer,
        accounting=DefaultTurnAccounting(Session(), "please connect to local llama"),
    )

    assert captured == [("provider:local_llama_connect",)]


def test_run_turn_clears_terminal_slash_dedup_at_turn_start() -> None:
    """Per-turn slash dedup lives on session.terminal; run_turn must clear it each turn."""
    session = Session()
    session.terminal.agent_turn_executed_slashes.add("/stale")

    def _noop_execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
            response_text="",
        )

    run_turn(
        "hi",
        session,
        execute_actions=_noop_execute,
        gather=lambda *_args, **_kwargs: None,
        answer=lambda *_args, **_kwargs: None,
        accounting=DefaultTurnAccounting(session, "hi"),
    )

    assert session.terminal.agent_turn_executed_slashes == set()


def test_stage_turn_error_routes_to_terminal_facet() -> None:
    """Structured error staging lives on session.terminal; stage_turn_error must reach it."""
    from core.agent_harness.turns.orchestrator import stage_turn_error

    session = Session()
    stage_turn_error(session, "provider_error", "boom")

    assert session.terminal.pop_pending_turn_error() == ("provider_error", "boom")


def test_pop_turn_outcome_hint_reads_terminal_facet() -> None:
    """Outcome hint lives on session.terminal; the driver helper must pop it from there."""
    from core.agent_harness.turns.action_driver import _pop_turn_outcome_hint

    session = Session()
    session.terminal.set_turn_outcome_hint("handled")

    assert _pop_turn_outcome_hint(session) == "handled"
    assert _pop_turn_outcome_hint(session) == ""


def test_run_turn_mixed_action_and_handoff_routes_to_assistant() -> None:
    """Handled action plus handoff tags must not take the handled_without_llm path."""
    captured: list[tuple[str, ...]] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="ran /health",
            handoff_contents=("provider:local_llama_connect",),
        )

    def _answer(*_args: Any, handoff_contents: tuple[str, ...] = (), **_kwargs: Any) -> None:
        captured.append(handoff_contents)
        return None

    result = run_turn(
        "check health and connect local llama",
        Session(),
        execute_actions=_execute,
        gather=lambda *_args, **_kwargs: None,
        answer=_answer,
        accounting=DefaultTurnAccounting(Session(), "check health and connect local llama"),
    )

    assert captured == [("provider:local_llama_connect",)]
    assert result.final_intent == "cli_agent_fallback"


def test_execute_with_harness_handles_llm_unavailable() -> None:
    def _raise() -> object:
        raise RuntimeError("action agent unavailable")

    session = Session()
    result = run_action_tool_turn(
        "action agent outage",
        session,
        Console(force_terminal=False),
        deps=ToolCallingDeps(llm_factory=_raise),
    )

    assert result.handled is True
    assert result.has_unhandled_clause is True
    assert result.planned_count == 0
    assert session.cli_agent_messages[-1] == ("assistant", "action agent unavailable")


def test_build_action_agent_returns_action_turn_plan() -> None:
    llm = FakeActionLLM([no_tool_response()])
    deps = ToolCallingDeps(llm_factory=lambda: llm)
    session = Session()

    plan = _build_action_agent(
        message="test message",
        session=session,
        agent_tools=[],
        turn_snapshot=None,
        resolved_integrations={},
        deps=deps,
        tool_hooks=None,
        tool_resources={},
        observer=lambda *_args, **_kwargs: None,
    )

    assert isinstance(plan, ActionTurnPlan)
    assert "test message" in plan.user_message
    assert plan.agent is not None


def test_turn_resolved_integrations_trusts_plan_without_reresolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a plan present, the resolved view is read from it — never re-resolved.

    Pins the single-resolve contract for the empty-integrations edge: an empty
    ``{}`` on the plan is authoritative, not a signal to resolve again.
    """
    from dataclasses import replace

    from core.agent_harness.turns.turn_plan import TurnPlan
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

    def _must_not_run(_session: object) -> dict:
        raise AssertionError("must not re-resolve when the plan is present")

    monkeypatch.setattr(
        "core.agent_harness.turns.action_driver.resolve_and_cache_integrations", _must_not_run
    )
    snapshot = replace(TurnSnapshot.from_session("q", Session()), resolved_integrations={})
    plan = TurnPlan(snapshot=snapshot)

    assert _turn_resolved_integrations(Session(), plan) == {}
