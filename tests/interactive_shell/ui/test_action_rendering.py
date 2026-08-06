"""Tests for interactive-shell action rendering."""

from __future__ import annotations

import io
from dataclasses import dataclass
from unittest.mock import Mock

import pytest
from rich.console import Console
from rich.text import Text

import surfaces.interactive_shell.runtime.slash_adapter as slash_adapter
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from platform.terminal.theme import BOLD_SKILL, HIGHLIGHT
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.action_rendering import ActionRenderObserver
from surfaces.interactive_shell.ui.input_prompt.rendering import (
    _prompt_turn_number,
    render_submitted_prompt,
)
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
)


def test_slash_invoke_tool_start_does_not_record_cli_agent() -> None:
    session = Session()
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
    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    observer = ActionRenderObserver(session=session, console=console, message="!true")

    observer("tool_start", {"name": "shell_run", "input": {"command": "true"}})

    assert session.history == []
    assert observer.planned_count == 1


def _observer_with_buffer(message: str = "onboard me") -> tuple[ActionRenderObserver, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=100)
    return ActionRenderObserver(session=Session(), console=console, message=message), buffer


def test_message_update_before_tool_calls_renders_live() -> None:
    """Phase headers preceding a tool group appear in the terminal as Markdown."""
    observer, buffer = _observer_with_buffer()

    observer(
        "message_update",
        {
            "content": "### [1/8] Prerequisite checks\nRunning GitHub CLI checks…",
            "has_tool_calls": True,
        },
    )

    output = buffer.getvalue()
    assert "[1/8] Prerequisite checks" in output
    assert "Running GitHub CLI checks" in output


def test_message_update_final_answer_is_not_rendered() -> None:
    """The closing no-tool-call answer is streamed by the turn driver, not here."""
    observer, buffer = _observer_with_buffer()

    observer("message_update", {"content": "All done.", "has_tool_calls": False})

    assert buffer.getvalue() == ""


def test_message_update_with_blank_content_prints_nothing() -> None:
    observer, buffer = _observer_with_buffer()

    observer("message_update", {"content": "   ", "has_tool_calls": True})

    assert buffer.getvalue() == ""


def _skill_observer() -> tuple[ActionRenderObserver, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=100)
    observer = ActionRenderObserver(session=Session(), console=console, message="run code review")
    return observer, buffer


def test_skill_view_renders_activation_event() -> None:
    """Loading a skill shows the two-line activation tree, nothing else."""
    observer, buffer = _skill_observer()

    observer(
        "tool_start",
        {"id": "t1", "name": "skill_view", "input": {"name": "install_code_review"}},
    )
    observer(
        "tool_end",
        {
            "id": "t1",
            "name": "skill_view",
            "input": {"name": "install_code_review"},
            "output": {"ok": True, "name": "install-code-review", "content": "<CDATA body>"},
        },
    )

    assert buffer.getvalue() == "\nSkill install-code-review\n  ↳ Skill activated\n\n"


def test_skill_view_renders_bold_green_skill_label() -> None:
    console = Mock(spec=Console)
    observer = ActionRenderObserver(session=Session(), console=console, message="run code review")

    observer(
        "tool_start",
        {"id": "t1", "name": "skill_view", "input": {"name": "install_code_review"}},
    )

    heading = console.print.call_args_list[1].args[0]
    assert isinstance(heading, Text)
    assert heading.plain == "Skill install-code-review"
    assert len(heading.spans) == 2
    assert str(heading.spans[0].style) == BOLD_SKILL
    assert str(heading.spans[1].style) == str(HIGHLIGHT)


def test_skill_view_failure_renders_failure_child() -> None:
    observer, buffer = _skill_observer()

    observer(
        "tool_start",
        {"id": "t1", "name": "skill_view", "input": {"name": "no-such-skill"}},
    )
    observer(
        "tool_end",
        {
            "id": "t1",
            "name": "skill_view",
            "input": {"name": "no-such-skill"},
            "output": {"error": "unknown skill 'no-such-skill'"},
        },
    )

    assert buffer.getvalue() == "\nSkill no-such-skill\n  ↳ Skill failed to load\n\n"


def test_skill_view_tool_end_without_start_prints_nothing() -> None:
    observer, buffer = _skill_observer()

    observer(
        "tool_end",
        {
            "id": "t9",
            "name": "skill_view",
            "input": {"name": "morning-report"},
            "output": {"ok": True},
        },
    )

    assert buffer.getvalue() == ""


def test_llm_start_advances_spinner_verb_every_two_steps() -> None:
    """The spinner verb re-rolls once per two agent steps, not every step."""
    from surfaces.interactive_shell.runtime.core.state import SpinnerState
    from surfaces.interactive_shell.ui.output.console_state import set_investigation_spinner

    observer, _buffer = _observer_with_buffer()
    spinner = SpinnerState()
    spinner.start()
    initial = spinner._verb
    set_investigation_spinner(spinner)
    try:
        observer("llm_start", {"iteration": 0})
        observer("llm_start", {"iteration": 1})
        assert spinner._verb == initial, "steps 0-1 must keep the verb picked at start()"
        observer("llm_start", {"iteration": 2})
        second = spinner._verb
        assert second != initial, "step 2 must rotate the verb"
        observer("llm_start", {"iteration": 3})
        assert spinner._verb == second, "step 3 must keep step 2's verb"
        observer("llm_start", {"iteration": 4})
        assert spinner._verb != second, "step 4 must rotate again"
    finally:
        set_investigation_spinner(None)


def test_llm_start_without_registered_spinner_is_noop() -> None:
    observer, buffer = _observer_with_buffer()

    observer("llm_start", {"iteration": 3})

    assert buffer.getvalue() == ""
    assert observer.planned_count == 0


def test_message_update_does_not_record_history_or_count_as_planned() -> None:
    observer, _buffer = _observer_with_buffer()

    observer("message_update", {"content": "### [1/8] Checks", "has_tool_calls": True})

    assert observer.session.history == []
    assert observer.planned_count == 0


def test_non_skill_tool_end_prints_nothing() -> None:
    observer, buffer = _skill_observer()

    observer("tool_start", {"id": "t1", "name": "shell_run", "input": {"command": "true"}})
    observer(
        "tool_end",
        {"id": "t1", "name": "shell_run", "input": {"command": "true"}, "output": {"ok": True}},
    )

    assert buffer.getvalue() == ""


def test_literal_slash_command_records_single_history_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)
    session = Session()
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))
    render_submitted_prompt(harness.console, session, "/model show")

    result = run_action_tool_turn(
        "/model show",
        session,
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert dispatched == ["/model show"]
    assert session.history == [{"type": "slash", "text": "/model show", "ok": True}]
    # The turn's history recording must not advance the prompt number; only the
    # submission itself does.
    assert _prompt_turn_number(session) == 2


@dataclass
class _FakeLlmRun:
    response_text: str = "hello back"


def test_chat_turn_records_single_cli_agent_history_entry() -> None:
    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    render_submitted_prompt(console, session, "what broke in prod?")

    def _no_actions(
        _text: str,
        _session: Session,
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
        _session: Session,
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
    # The turn's history recording must not advance the prompt number; only the
    # submission itself does.
    assert _prompt_turn_number(session) == 2
