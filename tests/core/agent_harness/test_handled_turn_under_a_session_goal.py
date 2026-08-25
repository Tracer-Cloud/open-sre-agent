"""A turn the action already answered must not be answered again by gather.

Asking "what's the current error rate on github?" under an active session goal
produced two different numbers in one turn: the action phase streamed 3.03%
(latest 100 runs) under ``OpenSRE``, then gather ran and streamed 5.14%
(24-hour window) under ``assistant``.

The action phase decides whether to paint its closing from the *planner's*
handoffs. The orchestrator then appends its own guidance tags — an evidence-tier
tag, and ``session_goal:continue`` while a goal is active — and used to hand the
augmented tuple to the router. ``route_turn`` reads a non-empty tuple as "the
planner asked the assistant to answer", so a handled turn became a gather turn
after its answer was already on screen.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import AnswerRequest
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult

_ANSWER = "GitHub Actions error rate: 3.03% — 3 failed out of 99 completed runs."


def _handled_by_the_action_phase(*_a: object, **_k: object) -> ToolCallingTurnResult:
    """The action ran a tool, answered, and emitted no planner handoff."""
    return ToolCallingTurnResult(
        planned_count=1,
        executed_count=1,
        executed_success_count=1,
        has_unhandled_clause=False,
        handled=True,
        response_text=_ANSWER,
        handoff_contents=(),
    )


def test_an_active_goal_does_not_send_a_handled_turn_through_gather() -> None:
    # Arrange: a goal is running, and the action phase answers on its own.
    session = SessionCore()
    attach_session_goal(
        session, SessionGoal(condition="report the github error rate", max_outer_turns=5)
    )
    answer_calls: list[str] = []

    def _answer(text: str, _request: AnswerRequest, **_k: Any) -> Any:
        answer_calls.append(text)
        return type("Run", (), {"response_text": "error rate: 5.14%"})()

    # Act
    result = run_turn(
        "what's the current error rate on github?",
        session,
        execute_actions=_handled_by_the_action_phase,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, "what's the current error rate on github?"),
    )

    # Assert: one answer, the action's — no second streamed number.
    assert answer_calls == []
    assert result.final_intent == "cli_agent_handled"
    assert "5.14%" not in (result.assistant_response_text or "")
