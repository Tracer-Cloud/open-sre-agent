"""Fallback answer path after an unhandled or summarized action turn."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.ports import AnswerRequest
from surfaces.interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from surfaces.interactive_shell.session import Session
from tests.shared.harness_turn_driver import run_harness_turn


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


def _record_answer() -> tuple[list[dict[str, Any]], Callable[..., None]]:
    calls: list[dict[str, Any]] = []

    def _fake_answer(
        message: str,
        session: Session,
        console: Console,
        *,
        request: AnswerRequest,
        **_kwargs: Any,
    ) -> None:
        calls.append(
            {
                "message": message,
                "tool_observation": request.tool_observation,
                "tool_observation_on_screen": request.tool_observation_on_screen,
            }
        )
        return None

    return calls, _fake_answer


def test_unhandled_turn_answers_without_tool_observation() -> None:
    calls, fake_answer = _record_answer()

    run_harness_turn(
        "question",
        Session(),
        _console(),
        recorder=None,
        execute_actions=_unhandled_turn,
        answer_agent=fake_answer,
    )

    assert len(calls) == 1
    assert calls[0]["tool_observation"] is None
    assert calls[0]["tool_observation_on_screen"] is True


def test_existing_command_observation_is_summarized() -> None:
    calls, fake_answer = _record_answer()

    def _handled_with_observation(
        _text: str,
        session: Session,
        _console: Console,
        **_kwargs: object,
    ) -> ToolCallingTurnResult:
        session.last_command_observation = "already gathered"
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
        )

    run_harness_turn(
        "question",
        Session(),
        _console(),
        recorder=None,
        execute_actions=_handled_with_observation,
        answer_agent=fake_answer,
    )

    assert len(calls) == 1
    assert calls[0]["tool_observation"] == "already gathered"
