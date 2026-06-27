"""Shell-local turn loop bookkeeping tests."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from interactive_shell.harness.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from interactive_shell.harness.turn_loop import ShellTurnContext, ShellTurnDeps, run_shell_turn
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.utils.telemetry.recorder import LlmRunInfo


class _Recorder:
    def __init__(self) -> None:
        self.responses: list[tuple[str, LlmRunInfo | None]] = []
        self.flush_count = 0

    def set_response(self, response: str, run_info: LlmRunInfo | None = None) -> None:
        self.responses.append((response, run_info))

    def flush(self) -> None:
        self.flush_count += 1


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


def test_recorder_flushes_once_for_chat_fallback() -> None:
    recorder = _Recorder()
    run_info = LlmRunInfo(response_text="answered")

    def _answer(*_args: Any, **_kwargs: Any) -> LlmRunInfo:
        return run_info

    result = run_shell_turn(
        ShellTurnContext(
            text="question",
            session=ReplSession(),
            console=_console(),
            recorder=recorder,  # type: ignore[arg-type]
        ),
        ShellTurnDeps(
            execute_actions=_unhandled_turn,
            gather_evidence=lambda *_a, **_k: None,
            answer_agent=_answer,
        ),
    )

    assert result.answered is True
    assert result.assistant_response_text == "answered"
    assert recorder.responses == [("answered", run_info)]
    assert recorder.flush_count == 1


def test_recorder_flushes_once_for_silent_handled_turn() -> None:
    recorder = _Recorder()

    def _handled(*_args: object, **_kwargs: object) -> TerminalActionExecutionResult:
        return TerminalActionExecutionResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="command output",
        )

    result = run_shell_turn(
        ShellTurnContext(
            text="run something",
            session=ReplSession(),
            console=_console(),
            recorder=recorder,  # type: ignore[arg-type]
        ),
        ShellTurnDeps(
            execute_actions=_handled,
            gather_evidence=lambda *_a, **_k: None,
            answer_agent=lambda *_a, **_k: None,
        ),
    )

    assert result.answered is False
    assert result.final_intent == "cli_agent_handled"
    assert recorder.responses == [("command output", None)]
    assert recorder.flush_count == 1
