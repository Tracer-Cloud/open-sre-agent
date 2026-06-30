"""Tests for interactive-shell action rendering."""

from __future__ import annotations

import io
from dataclasses import dataclass

import pytest
from rich.console import Console

import tools.interactive_shell.actions.slash as slash_tool
from core.agent_harness.session import ReplSession
from core.agent_harness.turn_results import ToolCallingTurnResult
from surfaces.interactive_shell.runtime.shell_turn_execution import (
    execute_shell_turn,
    run_action_tool_turn,
)
from surfaces.interactive_shell.ui.action_rendering import ActionRenderObserver
from surfaces.interactive_shell.ui.input_prompt.rendering import _prompt_turn_number
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
)


def test_slash_invoke_tool_start_does_not_record_cli_agent() -> None:
    session = ReplSession()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False)
    observer = ActionRenderObserver(session=session, console=console, message="/model show")

    observer(
        "tool_start",
        {"name": "slash_invoke", "input": {"command": "/model", "args": ["show"]}},
    )

    assert session.history == []
    assert observer.planned_count == 1
    assert buffer.getvalue() == ""


def test_shell_run_tool_start_does_not_record_cli_agent() -> None:
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    observer = ActionRenderObserver(session=session, console=console, message="!true")

    observer("tool_start", {"name": "shell_run", "input": {"command": "true"}})

    assert session.history == []
    assert observer.planned_count == 1


def test_literal_slash_command_records_single_history_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    session = ReplSession()
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))

    result = run_action_tool_turn(
        "/model show",
        session,
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert dispatched == ["/model show"]
    assert session.history == [{"type": "slash", "text": "/model show", "ok": True}]
    assert _prompt_turn_number(session) == 2


@dataclass
class _FakeLlmRun:
    response_text: str = "hello back"


def test_chat_turn_records_single_cli_agent_history_entry() -> None:
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    def _no_actions(
        _text: str,
        _session: ReplSession,
        _console: Console,
        **kwargs: object,
    ) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
            accounting_status="not_run",
        )

    def _answer(
        _text: str,
        _session: ReplSession,
        _console: Console,
        **kwargs: object,
    ) -> _FakeLlmRun:
        return _FakeLlmRun()

    execute_shell_turn(
        "what broke in prod?",
        session,
        console,
        recorder=None,
        execute_actions=_no_actions,
        answer_agent=_answer,
    )

    assert session.history == [{"type": "cli_agent", "text": "what broke in prod?", "ok": True}]
    assert _prompt_turn_number(session) == 2
