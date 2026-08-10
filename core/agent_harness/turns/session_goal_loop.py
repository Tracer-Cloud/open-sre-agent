"""Outer continuation loop around ``chat`` for an active :class:`SessionGoal`.

One iteration = one ``chat`` turn (always through the action agent). Goals are
attached explicitly or by structured action handoff tags — never by scanning
user prose.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from core.agent_harness.session.session_goal import (
    SessionGoal,
    SessionGoalStatus,
    apply_session_goal_progress,
    attach_session_goal,
    continuation_nudge,
    default_evaluate_session_goal,
    session_goal_is_active,
    strip_session_goal_progress_tags,
)
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult

ChatFn = Callable[[str], TurnResult]
EvaluateFn = Callable[..., str]
CancelFn = Callable[[], bool]
ProgressFn = Callable[[SessionGoal], None]


def _empty_turn_result() -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text="",
    )


def _reply_text(result: TurnResult) -> str:
    return (result.assistant_response_text or result.primary_response_text or "").strip()


def _refresh_active(session: Any, active: SessionGoal, result: TurnResult) -> SessionGoal:
    """Merge checklist progress from the reply and keep session in sync."""
    updated = apply_session_goal_progress(active, _reply_text(result))
    attach_session_goal(session, updated)
    return updated


def _scrub_progress_tags(result: TurnResult) -> TurnResult:
    """Hide ``session_goal:done=`` / ``achieved`` tokens from the user-visible reply."""
    raw = result.assistant_response_text or ""
    cleaned = strip_session_goal_progress_tags(raw)
    if cleaned == raw:
        return result
    return replace(result, assistant_response_text=cleaned)


@dataclass(slots=True)
class SessionGoalRunResult:
    """Outcome of :func:`run_until_session_goal`."""

    goal: SessionGoal
    last_result: TurnResult
    turn_count: int


def run_until_session_goal(
    chat: ChatFn,
    session: Any,
    message: str,
    *,
    goal: SessionGoal | None = None,
    evaluate: EvaluateFn | None = None,
    cancel_requested: CancelFn | None = None,
    on_progress: ProgressFn | None = None,
) -> SessionGoalRunResult:
    """Run ``chat`` until the session goal is terminal or the budget is hit.

    Always runs the first ``chat(message)`` through the action-agent path.
    Continues with nudges only when a goal is already active afterward
    (explicit ``goal=`` attach, or ``session_goal:`` handoff from that turn).
    """
    evaluate_fn = evaluate or default_evaluate_session_goal

    if goal is not None:
        attach_session_goal(session, goal)

    last = chat(message)
    active = getattr(session, "session_goal", None)
    if not isinstance(active, SessionGoal) or not session_goal_is_active(session):
        synthetic = SessionGoal(
            condition=message.strip() or "(none)",
            max_outer_turns=1,
            status=SessionGoalStatus.CLEARED,
            turns_used=1,
        )
        return SessionGoalRunResult(goal=synthetic, last_result=last, turn_count=1)

    if active.turns_used == 0:
        active = active.record_turn()
    active = _refresh_active(session, active, last)
    if on_progress is not None:
        on_progress(active)

    if last.cancelled:
        active = active.with_status(SessionGoalStatus.CANCELLED)
        attach_session_goal(session, active)
        return SessionGoalRunResult(
            goal=active, last_result=_scrub_progress_tags(last), turn_count=active.turns_used
        )

    if getattr(session, "pending_user_choice", None) is not None:
        return SessionGoalRunResult(
            goal=active, last_result=_scrub_progress_tags(last), turn_count=active.turns_used
        )

    # Evaluate on raw text (tags still present), then scrub for the user.
    next_status = evaluate_fn(active, last, session=session)
    stored = getattr(session, "session_goal", None)
    if isinstance(stored, SessionGoal):
        active = stored
    last = _scrub_progress_tags(last)
    if next_status != SessionGoalStatus.ACTIVE:
        active = active.with_status(next_status)
        attach_session_goal(session, active)
        if on_progress is not None:
            on_progress(active)
        return SessionGoalRunResult(goal=active, last_result=last, turn_count=active.turns_used)

    while active.status == SessionGoalStatus.ACTIVE:
        if cancel_requested is not None and cancel_requested():
            active = active.with_status(SessionGoalStatus.CANCELLED)
            attach_session_goal(session, active)
            break

        if active.turns_used >= active.max_outer_turns:
            active = active.with_status(SessionGoalStatus.BUDGET_EXHAUSTED)
            attach_session_goal(session, active)
            break

        last = chat(continuation_nudge(active))
        active = active.record_turn()
        active = _refresh_active(session, active, last)
        if on_progress is not None:
            on_progress(active)

        if last.cancelled:
            active = active.with_status(SessionGoalStatus.CANCELLED)
            attach_session_goal(session, active)
            last = _scrub_progress_tags(last)
            break

        if getattr(session, "pending_user_choice", None) is not None:
            last = _scrub_progress_tags(last)
            break

        next_status = evaluate_fn(active, last, session=session)
        stored = getattr(session, "session_goal", None)
        if isinstance(stored, SessionGoal):
            active = stored
        last = _scrub_progress_tags(last)
        if next_status != SessionGoalStatus.ACTIVE:
            active = active.with_status(next_status)
            attach_session_goal(session, active)
            if on_progress is not None:
                on_progress(active)
            break

        if active.turns_used >= active.max_outer_turns:
            active = active.with_status(SessionGoalStatus.BUDGET_EXHAUSTED)
            attach_session_goal(session, active)
            break

    if last is None:
        last = _empty_turn_result()

    stored = getattr(session, "session_goal", None)
    if isinstance(stored, SessionGoal):
        active = stored

    return SessionGoalRunResult(
        goal=active,
        last_result=last,
        turn_count=active.turns_used,
    )


__all__ = [
    "SessionGoalRunResult",
    "run_until_session_goal",
    "session_goal_is_active",
]
