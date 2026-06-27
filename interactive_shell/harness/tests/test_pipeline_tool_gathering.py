"""Tool-gathering behavior for interactive-shell pipeline fallback turns."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from rich.console import Console

from interactive_shell.harness.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from interactive_shell.harness.turn_loop import ShellTurnContext, ShellTurnDeps, run_shell_turn
from interactive_shell.runtime.core.session import ReplSession


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=80)


def _unhandled_turn(*_args: object, **_kwargs: object) -> TerminalActionExecutionResult:
    return TerminalActionExecutionResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=False,
        handled=False,
    )


def _record_answer() -> tuple[list[dict[str, Any]], Callable[..., None]]:
    calls: list[dict[str, Any]] = []

    def _fake_answer(message: str, session: ReplSession, console: Console, **kwargs: Any) -> None:
        calls.append({"message": message, **kwargs})
        return None

    return calls, _fake_answer


def test_gather_string_threads_offscreen_observation() -> None:
    calls, fake_answer = _record_answer()

    run_shell_turn(
        ShellTurnContext(
            text="question",
            session=ReplSession(),
            console=_console(),
            recorder=None,
        ),
        ShellTurnDeps(
            execute_actions=_unhandled_turn,
            gather_evidence=lambda *_a, **_k: "Tool: x\nArguments: {}\nResult: y",
            answer_agent=fake_answer,
        ),
    )

    assert len(calls) == 1
    assert calls[0]["tool_observation"] == "Tool: x\nArguments: {}\nResult: y"
    assert calls[0]["tool_observation_on_screen"] is False


def test_gather_none_passes_through_without_observation() -> None:
    calls, fake_answer = _record_answer()

    run_shell_turn(
        ShellTurnContext(
            text="question",
            session=ReplSession(),
            console=_console(),
            recorder=None,
        ),
        ShellTurnDeps(
            execute_actions=_unhandled_turn,
            gather_evidence=lambda *_a, **_k: None,
            answer_agent=fake_answer,
        ),
    )

    assert len(calls) == 1
    assert calls[0]["tool_observation"] is None
    assert "tool_observation_on_screen" not in calls[0]


def test_existing_command_observation_skips_gather() -> None:
    calls, fake_answer = _record_answer()

    def _should_not_run(*_a: Any, **_k: Any) -> str:
        raise AssertionError("gather_tool_evidence must not run on the summarize path")

    def _handled_with_observation(
        _text: str,
        session: ReplSession,
        _console: Console,
        **_kwargs: object,
    ) -> TerminalActionExecutionResult:
        session.last_command_observation = "already gathered"
        return TerminalActionExecutionResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
        )

    run_shell_turn(
        ShellTurnContext(
            text="question",
            session=ReplSession(),
            console=_console(),
            recorder=None,
        ),
        ShellTurnDeps(
            execute_actions=_handled_with_observation,
            gather_evidence=_should_not_run,
            answer_agent=fake_answer,
        ),
    )

    assert len(calls) == 1
    assert calls[0]["tool_observation"] == "already gathered"
