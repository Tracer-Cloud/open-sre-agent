"""Action-execution tests over model tool calls, not planner DTOs."""

from __future__ import annotations

from rich.console import Console

import interactive_shell.tools.slash_tool as slash_tool
from context.session import ReplSession
from interactive_shell.agent_shell.tool_calling import (
    ToolCallingDeps,
    run_tool_calling_turn,
)
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


def test_execute_with_harness_runs_slash_tool_call(monkeypatch) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
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
    session = ReplSession()

    result = run_tool_calling_turn(
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


def test_literal_slash_command_does_not_short_circuit_without_agent_tool_call(
    monkeypatch,
) -> None:
    def _unexpected_dispatch(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("literal slash commands must be selected by the action agent")

    monkeypatch.setattr(slash_tool, "dispatch_slash", _unexpected_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))

    result = run_tool_calling_turn(
        "/sessions",
        ReplSession(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is False
    assert result.planned_count == 0


def test_execute_with_harness_hands_off_handoff_only_tool_call() -> None:
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [tool_response("assistant_handoff", {"content": "docs:help"})],
        )
    )

    result = run_tool_calling_turn(
        "half actionable prompt",
        ReplSession(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is False
    assert result.has_unhandled_clause is False
    assert result.planned_count == 0
    assert "Requested actions" not in harness.console_buffer.getvalue()


def test_execute_with_harness_handles_llm_unavailable() -> None:
    def _raise() -> object:
        raise RuntimeError("action agent unavailable")

    session = ReplSession()
    result = run_tool_calling_turn(
        "action agent outage",
        session,
        Console(force_terminal=False),
        deps=ToolCallingDeps(llm_factory=_raise),
    )

    assert result.handled is True
    assert result.has_unhandled_clause is True
    assert result.planned_count == 0
    assert session.cli_agent_messages[-1] == ("assistant", "action agent unavailable")
