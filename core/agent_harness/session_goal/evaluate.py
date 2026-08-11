"""SessionGoal completion — structured verdict, not model self-report alone.

The action/assistant model may emit ``session_goal:achieved``. That tag is a
claim, not proof. This module is the independent host check:

* Checklist complete (via ``done=`` indices) → achieved.
* ``achieved`` with an incomplete checklist → stay active (ignore the tag).
* ``achieved`` while ``investigation_dispatched`` this turn → stay active
  (starting RCA is not finishing the goal).
* ``achieved`` on a **host-owned** (``/goal set``) goal → achieved without tools
  when no investigation was dispatched (explicit slash-path product rule).
* ``achieved`` with no checklist on a handoff goal → require tool evidence, or
  stay active.
* Hosts may wrap :func:`evaluate_session_goal` with an LLM confirm for the
  tool-evidence path (:mod:`core.agent_harness.session_goal.confirm`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    apply_session_goal_progress,
    attach_session_goal,
)

# Standalone progress tag — same token shape as strip_session_goal_progress_tags.
_ACHIEVED_CLAIM = re.compile(
    r"(?:^|\s)session_goal:achieved(?=\s|$)",
    re.MULTILINE,
)

# Pre-fix host reasons embedded the tag grammar; neutralize before scanning so
# old painted status text cannot look like a claim.
_LEGACY_WAITING_WITH_TAG = (
    "waiting for session_goal:achieved with tool evidence",
    "waiting for session_goal:achieved",
)


@dataclass(frozen=True, slots=True)
class SessionGoalVerdict:
    """Host decision for one session-goal evaluation."""

    status: str
    reason: str


def session_goal_reply_text(result: Any) -> str:
    """Best assistant reply text from a turn result (evaluate / loop shared)."""
    response = getattr(result, "assistant_response_text", None)
    if isinstance(response, str) and response:
        return response
    primary = getattr(result, "primary_response_text", None)
    if isinstance(primary, str):
        return primary
    return ""


def reply_claims_session_goal_achieved(text: str) -> bool:
    """True when ``text`` contains a real ``session_goal:achieved`` progress tag.

    Host status reasons never embed tag grammar (:class:`SessionGoalReason`).
    Legacy painted phrases that did are stripped before the token scan.
    """
    if not text:
        return False
    scrubbed = text
    for phrase in _LEGACY_WAITING_WITH_TAG:
        scrubbed = scrubbed.replace(phrase, "")
    return _ACHIEVED_CLAIM.search(scrubbed) is not None


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

    Dispatching ``investigation_start`` is not finishing evidence for a session
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
    """Independent structured evaluation of an session goal."""
    if session is not None and getattr(session, "pending_user_choice", None) is not None:
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=SessionGoalReason.WAITING_USER_CHOICE,
        )

    text = session_goal_reply_text(result)
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
                reason=SessionGoalReason.CHECKLIST_COMPLETE,
            )
        elif claimed:
            nxt = current.next_checklist_item
            next_label = nxt[1] if nxt is not None else None
            done = len(current.completed & frozenset(range(len(current.checklist))))
            total = len(current.checklist)
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.achieved_ignored_incomplete(done, total, next_label),
            )
        else:
            done = len(current.completed & frozenset(range(len(current.checklist))))
            total = len(current.checklist)
            nxt = current.next_checklist_item
            if nxt is None:
                reason = SessionGoalReason.checklist_progress(done, total)
            else:
                reason = SessionGoalReason.checklist_progress(done, total, nxt[1])
            verdict = SessionGoalVerdict(status=SessionGoalStatus.ACTIVE, reason=reason)
    elif claimed and dispatched:
        verdict = SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=SessionGoalReason.ACHIEVED_IGNORED_INVESTIGATION,
        )
    elif claimed:
        if current.host_owned or turn_has_session_goal_evidence(result):
            soft_host = current.host_owned and not turn_has_session_goal_evidence(result)
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=(
                    SessionGoalReason.ACHIEVED_HOST_SET
                    if soft_host
                    else SessionGoalReason.ACHIEVED_TOOL_EVIDENCE
                ),
            )
        else:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.NO_TOOL_EVIDENCE,
            )
    else:
        if dispatched:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.INVESTIGATION_RUNNING,
            )
        elif current.host_owned:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.WAITING_HOST_SIGNAL,
            )
        else:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.WAITING_TOOL_EVIDENCE,
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
    "session_goal_reply_text",
    "turn_dispatched_investigation",
    "turn_has_session_goal_evidence",
]
