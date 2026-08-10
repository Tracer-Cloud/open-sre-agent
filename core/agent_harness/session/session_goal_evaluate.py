"""Outer SessionGoal completion — structured verdict, not model self-report alone.

The action/assistant model may emit ``session_goal:achieved``. That tag is a
claim, not proof. This module is the independent host check:

* Checklist complete (via ``done=`` indices) → achieved.
* ``achieved`` with an incomplete checklist → stay active (ignore the tag).
* ``achieved`` while ``investigation_dispatched`` this turn → stay active
  (starting RCA is not finishing the goal).
* ``achieved`` on a **host-owned** (``/goal set``) goal → achieved without tools
  when no investigation was dispatched.
* ``achieved`` with no checklist on a handoff goal → require tool evidence, or
  stay active.
* Hosts may wrap :func:`evaluate_session_goal` with an LLM confirm for the
  tool-evidence path (:mod:`session_goal_review`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.agent_harness.session.session_goal import (
    SessionGoal,
    SessionGoalStatus,
    apply_session_goal_progress,
    attach_session_goal,
)

# Standalone progress tag only — must not match host reason phrases like
# ``waiting for session_goal:achieved`` (substring false positive on /goal set).
_ACHIEVED_CLAIM = re.compile(
    r"(?<!waiting for )session_goal:achieved(?![A-Za-z0-9_])",
)


@dataclass(frozen=True, slots=True)
class SessionGoalVerdict:
    """Host decision for one outer-loop evaluation."""

    status: str
    reason: str


def reply_claims_session_goal_achieved(text: str) -> bool:
    """True when ``text`` contains a real ``session_goal:achieved`` progress tag.

    Host status lines say ``waiting for session_goal:achieved``; a naive
    substring check would treat that phrase as a completion claim (and did, on
    the ``/goal set`` turn that prints the waiting reason).
    """
    if not text:
        return False
    return _ACHIEVED_CLAIM.search(text) is not None


def _reply_text(result: Any) -> str:
    text = ""
    response = getattr(result, "assistant_response_text", None)
    if isinstance(response, str):
        text = response
    if text:
        return text
    primary = getattr(result, "primary_response_text", None)
    if isinstance(primary, str):
        return primary
    return ""


def turn_dispatched_investigation(result: Any) -> bool:
    """True when this turn started an RCA pipeline (not yet a finished answer)."""
    action = getattr(result, "action_result", None)
    if action is None:
        return False
    return bool(getattr(action, "investigation_dispatched", False))


def turn_has_session_goal_evidence(result: Any) -> bool:
    """True when the turn ran a tool **successfully** — not prose, not a claim.

    A tool that ran and errored is not evidence the goal was met, so a failed
    call must not let an ``achieved`` claim through. ``executed_count`` alone
    would say yes to a turn whose only action failed.

    Dispatching ``investigation_start`` is not finishing evidence for an outer
    goal — that work lands in later turns / the investigation report.
    """
    if turn_dispatched_investigation(result):
        return False
    action = getattr(result, "action_result", None)
    if action is None:
        return False
    try:
        succeeded = int(getattr(action, "executed_success_count", 0) or 0)
    except (TypeError, ValueError):
        return False
    return succeeded > 0


def evaluate_session_goal(
    goal: SessionGoal,
    result: Any,
    *,
    session: Any | None = None,
) -> SessionGoalVerdict:
    """Independent structured evaluation of an outer session goal."""
    if session is not None and getattr(session, "pending_user_choice", None) is not None:
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason="waiting for user choice",
        )

    text = _reply_text(result)
    current = goal
    if session is not None:
        stored = getattr(session, "session_goal", None)
        if isinstance(stored, SessionGoal):
            current = stored
    current = apply_session_goal_progress(current, text)

    claimed = reply_claims_session_goal_achieved(text)
    dispatched = turn_dispatched_investigation(result)

    if current.checklist:
        if current.checklist_complete:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason="checklist complete",
            )
        elif claimed:
            nxt = current.next_checklist_item
            next_bit = f" — next: {nxt[1]}" if nxt is not None else ""
            done = len(current.completed & frozenset(range(len(current.checklist))))
            total = len(current.checklist)
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=(f"achieved tag ignored; checklist {done}/{total} incomplete{next_bit}"),
            )
        else:
            done = len(current.completed & frozenset(range(len(current.checklist))))
            total = len(current.checklist)
            nxt = current.next_checklist_item
            if nxt is None:
                reason = f"checklist {done}/{total} done"
            else:
                reason = f"checklist {done}/{total} done — next: {nxt[1]}"
            verdict = SessionGoalVerdict(status=SessionGoalStatus.ACTIVE, reason=reason)
    elif claimed and dispatched:
        verdict = SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason="achieved tag ignored; investigation still running",
        )
    elif claimed:
        if current.host_owned or turn_has_session_goal_evidence(result):
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=(
                    "achieved (host-set goal)"
                    if current.host_owned and not turn_has_session_goal_evidence(result)
                    else "achieved with tool evidence"
                ),
            )
        else:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason="achieved tag ignored; no tool evidence yet",
            )
    else:
        if dispatched:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason="investigation running — continue after results",
            )
        elif current.host_owned:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason="waiting for session_goal:achieved",
            )
        else:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason="waiting for session_goal:achieved with tool evidence",
            )

    if session is not None:
        updated = current.with_status(verdict.status).with_reason(verdict.reason)
        attach_session_goal(session, updated)
    return verdict


def default_evaluate_session_goal(
    goal: SessionGoal,
    result: Any,
    *,
    session: Any | None = None,
) -> str:
    """Loop-facing evaluate: status string; reason stored on the session goal."""
    return evaluate_session_goal(goal, result, session=session).status


__all__ = [
    "SessionGoalVerdict",
    "default_evaluate_session_goal",
    "evaluate_session_goal",
    "reply_claims_session_goal_achieved",
    "turn_dispatched_investigation",
    "turn_has_session_goal_evidence",
]
