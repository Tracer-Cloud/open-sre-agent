"""Shell-local turn loop bookkeeping tests."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from interactive_shell.agent_shell.agent import AgentTurnRunner, handle_message_with_agent
from interactive_shell.runtime.core.state import ReplState, SpinnerState
from interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from interactive_shell.session import ReplSession
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


def _unhandled_turn(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
    return ToolCallingTurnResult(
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

    result = handle_message_with_agent(
        "question",
        ReplSession(),
        _console(),
        recorder=recorder,  # type: ignore[arg-type]
        execute_actions=_unhandled_turn,
        gather_evidence=lambda *_a, **_k: None,
        answer_agent=_answer,
    )

    assert result.answered is True
    assert result.assistant_response_text == "answered"
    assert recorder.responses == [("answered", run_info)]
    assert recorder.flush_count == 1


def test_recorder_flushes_once_for_silent_handled_turn() -> None:
    recorder = _Recorder()

    def _handled(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="command output",
        )

    result = handle_message_with_agent(
        "run something",
        ReplSession(),
        _console(),
        recorder=recorder,  # type: ignore[arg-type]
        execute_actions=_handled,
        gather_evidence=lambda *_a, **_k: None,
        answer_agent=lambda *_a, **_k: None,
    )

    assert result.answered is False
    assert result.final_intent == "cli_agent_handled"
    assert recorder.responses == [("command output", None)]
    assert recorder.flush_count == 1


def test_agent_turn_runner_exposes_pi_style_queue_methods() -> None:
    state = ReplState()
    runner = AgentTurnRunner(
        session=ReplSession(),
        state=state,
        spinner=SpinnerState(),
        invalidate_prompt=lambda: None,
    )

    runner.steer(" steer the current work ")
    runner.follow_up(" follow up ")
    runner.next_turn(" next turn ")
    runner.followUp(" camel follow ")
    runner.nextTurn(" camel next ")

    queued: list[str] = []
    while not state.queue.empty():
        queued.append(state.queue.get_nowait())
        state.queue.task_done()

    assert queued == [
        "steer the current work",
        "follow up",
        "next turn",
        "camel follow",
        "camel next",
    ]
