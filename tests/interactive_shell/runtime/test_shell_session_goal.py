"""Shell continues SessionGoal only after structured action handoff."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

from rich.console import Console

from core.agent_harness.session_goal.goal import SessionGoalStatus
from core.agent_harness.turns.assistant_handoff import AssistantHandoff
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn
from surfaces.interactive_shell.session import Session

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_FIVE_STEP = (
    "Do this 5-step sequential process without asking whether to continue: "
    "(1) a (2) b (3) c (4) d (5) e."
)


def test_execute_shell_turn_continues_when_action_emits_session_goal() -> None:
    session = Session()
    console = Console(force_terminal=False)
    answer_calls: list[str] = []
    execute_calls = 0

    def _execute(text: str, *_a: object, **_k: object) -> ToolCallingTurnResult:
        nonlocal execute_calls
        execute_calls += 1
        handoffs: tuple[str, ...]
        if execute_calls == 1:
            handoffs = (
                "session_goal:continue",
                "session_goal_max_turns:5",
                "session_goal_item:a",
                "session_goal_item:b",
                "session_goal_item:c",
                "session_goal_item:d",
                "session_goal_item:e",
            )
        else:
            handoffs = ("Continue the checklist.",)
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=handoffs,
        )

    def _gather(*_a: object, **_k: object) -> str:
        return "no live evidence"

    def _answer(text: str, *_a: object, **_k: object) -> MagicMock:
        answer_calls.append(text)
        n = len(answer_calls)
        return MagicMock(response_text=f"step done session_goal:done={n - 1}")

    result = execute_shell_turn(
        _FIVE_STEP,
        session,
        console,
        recorder=None,
        is_tty=False,
        execute_actions=_execute,
        gather_evidence=_gather,
        answer_agent=_answer,
    )

    assert len(answer_calls) == 5
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACHIEVED
    assert "session_goal:" not in (result.assistant_response_text or "")


def test_execute_shell_turn_prints_checklist_progress(capsys: Any) -> None:
    session = Session()
    console = Console(force_terminal=True)
    answer_calls: list[str] = []

    def _execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=(
                "session_goal:max_turns=3",
                "session_goal_item:Alpha",
                "session_goal_item:Beta",
            ),
        )

    def _answer(*_a: object, **_k: object) -> MagicMock:
        answer_calls.append("x")
        n = len(answer_calls)
        return MagicMock(response_text=f"session_goal:done={n - 1}")

    execute_shell_turn(
        "run checklist",
        session,
        console,
        recorder=None,
        is_tty=False,
        execute_actions=_execute,
        gather_evidence=lambda *_a, **_k: "e",
        answer_agent=_answer,
    )

    # Rich may color ``/goal`` when force_terminal=True, so strip SGR first.
    painted = _ANSI.sub("", capsys.readouterr().out)
    assert "◎ /goal" in painted
    assert "Checklist:" in painted
    assert "Alpha" in painted and "Beta" in painted
    assert "turn " in painted
    assert "reason:" in painted
    # Continuation paints working… (not a second evaluate block), then achieved.
    assert "working" in painted
    assert "checklist complete" in painted or "achieved" in painted


def test_execute_shell_turn_does_not_loop_on_user_prose_alone() -> None:
    session = Session()
    console = Console(force_terminal=False)
    answer_calls: list[str] = []

    def _execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=("Ordinary handoff with no session_goal tag.",),
        )

    def _answer(text: str, *_a: object, **_k: object) -> MagicMock:
        answer_calls.append(text)
        return MagicMock(response_text="one turn only")

    execute_shell_turn(
        _FIVE_STEP,
        session,
        console,
        recorder=None,
        is_tty=False,
        execute_actions=_execute,
        gather_evidence=lambda *_a, **_k: "e",
        answer_agent=_answer,
    )

    assert len(answer_calls) == 1
    assert session.session_goal is None


def test_execute_shell_turn_continues_from_typed_assistant_handoff() -> None:
    """Schema-only session_goal (no content tags) must still multi-turn."""
    session = Session()
    console = Console(force_terminal=False)
    answer_calls: list[str] = []
    execute_calls = 0

    def _execute(text: str, *_a: object, **_k: object) -> ToolCallingTurnResult:
        nonlocal execute_calls
        execute_calls += 1
        if execute_calls == 1:
            return ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=True,
                handled=False,
                handoff_contents=("Work the checklist.",),
                assistant_handoffs=(
                    AssistantHandoff(
                        content="Work the checklist.",
                        session_goal="max_turns=5;steps=5",
                        session_goal_items=("a", "b", "c", "d", "e"),
                    ),
                ),
            )
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=("Continue.",),
        )

    def _answer(text: str, *_a: object, **_k: object) -> MagicMock:
        answer_calls.append(text)
        n = len(answer_calls)
        return MagicMock(response_text=f"step done session_goal:done={n - 1}")

    result = execute_shell_turn(
        _FIVE_STEP,
        session,
        console,
        recorder=None,
        is_tty=False,
        execute_actions=_execute,
        gather_evidence=lambda *_a, **_k: "e",
        answer_agent=_answer,
    )

    assert len(answer_calls) == 5
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACHIEVED
    assert session.session_goal.checklist == ("a", "b", "c", "d", "e")
    assert "session_goal:" not in (result.assistant_response_text or "")
