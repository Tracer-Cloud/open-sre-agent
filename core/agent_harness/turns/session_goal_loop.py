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
    refresh_session_goal_reason,
    session_goal_is_active,
    strip_session_goal_progress_tags,
)
from core.agent_harness.session.session_goal_evaluate import default_evaluate_session_goal
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
    """Hide ``session_goal:done=`` / ``achieved`` tokens from the user-visible reply.

    Scrubs both ``assistant_response_text`` and any action ``response_text`` so
    shell and gateway ``primary_response_text`` stay tag-free.
    """
    assistant = result.assistant_response_text or ""
    cleaned_assistant = strip_session_goal_progress_tags(assistant)
    action = result.action_result
    action_text = getattr(action, "response_text", "") or ""
    cleaned_action = strip_session_goal_progress_tags(action_text)
    if cleaned_assistant == assistant and cleaned_action == action_text:
        return result
    new_action = action
    if cleaned_action != action_text and hasattr(action, "response_text"):
        new_action = replace(action, response_text=cleaned_action)
    return replace(
        result,
        assistant_response_text=cleaned_assistant,
        action_result=new_action,
    )


def _paint(
    session: Any,
    active: SessionGoal,
    on_progress: ProgressFn | None,
    *,
    rederive: bool = True,
) -> SessionGoal:
    """Notify the host of progress.

    When ``rederive`` is false, keep ``active.last_reason`` (e.g. the evaluate
    verdict) so we do not flash a stale "waiting…" line before "achieved".
    """
    painted = refresh_session_goal_reason(active) if rederive else active
    if not painted.last_reason.strip():
        painted = refresh_session_goal_reason(painted)
    attach_session_goal(session, painted)
    if on_progress is not None:
        on_progress(painted)
    return painted


def _announce_working(
    session: Any,
    active: SessionGoal,
    on_progress: ProgressFn | None,
) -> SessionGoal:
    """Paint a clear 'working now' line before an outer ``chat`` starts."""
    next_turn = min(active.turns_used + 1, active.max_outer_turns)
    working = active.with_reason(
        f"working — starting outer turn {next_turn}/{active.max_outer_turns}"
    )
    attach_session_goal(session, working)
    if on_progress is not None:
        on_progress(working)
    return working


def _finish_outer_turn(
    session: Any,
    active: SessionGoal,
    last: TurnResult,
    *,
    evaluate_fn: EvaluateFn,
    on_progress: ProgressFn | None,
) -> tuple[SessionGoal, TurnResult, bool]:
    """Refresh → evaluate → single paint. Returns ``(goal, scrubbed, stop)``."""
    active = _refresh_active(session, active, last)

    if last.cancelled:
        active = active.with_status(SessionGoalStatus.CANCELLED)
        active = _paint(session, active, on_progress)
        return active, _scrub_progress_tags(last), True

    if getattr(session, "pending_user_choice", None) is not None:
        active = active.with_reason("paused — waiting for your choice")
        active = _paint(session, active, on_progress, rederive=False)
        return active, _scrub_progress_tags(last), True

    next_status = evaluate_fn(active, last, session=session)
    stored = getattr(session, "session_goal", None)
    if isinstance(stored, SessionGoal):
        active = stored
    # Evaluate return is authoritative — optional reviewers may keep ACTIVE after
    # structured evaluate briefly attached ACHIEVED on the session.
    if active.status != next_status:
        active = active.with_status(next_status)
        attach_session_goal(session, active)
    last = _scrub_progress_tags(last)

    if next_status != SessionGoalStatus.ACTIVE:
        active = active.with_status(next_status)
        active = _paint(session, active, on_progress, rederive=False)
        return active, last, True

    if active.turns_used >= active.max_outer_turns:
        active = active.with_status(SessionGoalStatus.BUDGET_EXHAUSTED)
        active = _paint(session, active, on_progress)
        return active, last, True

    # Still active under budget: one paint with the evaluate reason.
    active = _paint(session, active, on_progress, rederive=False)
    return active, last, False


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

    pre = getattr(session, "session_goal", None)
    had_active_before = False
    if isinstance(pre, SessionGoal) and pre.status == SessionGoalStatus.ACTIVE:
        had_active_before = True
        _announce_working(session, pre, on_progress)

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

    # ``/goal set`` attaches a host-owned goal mid-turn and queues autosubmit.
    # That attach turn must not count against the budget or run evaluate —
    # the next submitted condition is the first real outer turn.
    if not had_active_before and active.host_owned and active.turns_used == 0:
        return SessionGoalRunResult(goal=active, last_result=last, turn_count=0)

    if active.turns_used == 0:
        active = active.record_turn()

    active, last, stop = _finish_outer_turn(
        session,
        active,
        last,
        evaluate_fn=evaluate_fn,
        on_progress=on_progress,
    )
    if stop:
        return SessionGoalRunResult(goal=active, last_result=last, turn_count=active.turns_used)

    while active.status == SessionGoalStatus.ACTIVE:
        if cancel_requested is not None and cancel_requested():
            active = active.with_status(SessionGoalStatus.CANCELLED)
            active = _paint(session, active, on_progress)
            break

        if active.turns_used >= active.max_outer_turns:
            active = active.with_status(SessionGoalStatus.BUDGET_EXHAUSTED)
            active = _paint(session, active, on_progress)
            break

        _announce_working(session, active, on_progress)
        last = chat(continuation_nudge(active))
        active = active.record_turn()
        active, last, stop = _finish_outer_turn(
            session,
            active,
            last,
            evaluate_fn=evaluate_fn,
            on_progress=on_progress,
        )
        if stop:
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
