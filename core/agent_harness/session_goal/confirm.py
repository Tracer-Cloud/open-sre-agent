"""Optional LLM confirm for SessionGoal after structured evidence passes.

Structured :func:`~core.agent_harness.session_goal.evaluate.evaluate_session_goal`
is the default host judge. This module adds a second, independent model check
only when that judge would achieve a **condition-only** goal via the achieved
tag + tool evidence path.

Checklist completion stays structured-only (done indices are already host-tracked).
Fails open to ACTIVE on LLM errors so a broken reviewer cannot false-complete.

Confirm uses :func:`~core.agent_harness.closed_llm_verdict.invoke_closed_goal_verdict`
(closed ``Literal`` enum) — not free-text scrape.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.closed_llm_verdict import invoke_closed_goal_verdict
from core.agent_harness.session_goal.evaluate import (
    evaluate_session_goal,
    session_goal_reply_text,
)
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
)
from core.llm.types import AgentLLMClient

_REVIEW_SYSTEM = (
    "You review whether an session goal is met.\n"
    "Return JSON only. Set verdict to GOAL_REACHED only when the assistant "
    "reply plus tools clearly satisfy the goal condition. When in doubt, "
    "set verdict to NOT_REACHED."
)


# The reviewer only needs the closing reply to judge the condition; a longer
# tail costs tokens on every session-goal turn without changing the verdict.
MAX_REVIEWED_REPLY_CHARS = 4000


def _llm_confirms_achieved(
    llm: AgentLLMClient,
    *,
    condition: str,
    reply: str,
) -> bool | None:
    """Return True/False from the model, or None on error / unclear."""
    prompt = (
        f"Goal condition:\n{condition}\n\n"
        f"Latest assistant reply:\n{reply[:MAX_REVIEWED_REPLY_CHARS]}\n\n"
        "Is the goal reached?"
    )
    verdict = invoke_closed_goal_verdict(llm, prompt=prompt, system=_REVIEW_SYSTEM)
    if verdict == "NOT_REACHED":
        return False
    if verdict == "GOAL_REACHED":
        return True
    return None


def build_session_goal_llm_evaluator(llm: AgentLLMClient):
    """Return an ``evaluate(goal, result, *, session=) -> status`` for the session-goal loop.

    Uses structured evaluate first. When that would achieve via tool evidence
    (no checklist), asks ``llm`` to confirm. Checklist achieves are trusted.
    """

    def _evaluate(
        goal: SessionGoal,
        result: Any,
        *,
        session: Any | None = None,
    ) -> str:
        verdict = evaluate_session_goal(goal, result, session=session)
        if (
            verdict.status != SessionGoalStatus.ACHIEVED
            or verdict.reason != SessionGoalReason.ACHIEVED_TOOL_EVIDENCE
        ):
            return verdict.status

        reply = session_goal_reply_text(result)

        confirmed = _llm_confirms_achieved(llm, condition=goal.condition, reply=reply)
        if confirmed is True:
            return SessionGoalStatus.ACHIEVED
        # False or None → keep working (never false-complete on reviewer failure).
        # Structured evaluate may already have attached ACHIEVED on ``session``;
        # overwrite status so the session-goal loop's session reread cannot defeat this.
        reason = (
            SessionGoalReason.LLM_CONFIRM_NOT_REACHED
            if confirmed is False
            else SessionGoalReason.LLM_CONFIRM_UNAVAILABLE
        )
        if session is not None:
            stored = getattr(session, "session_goal", None)
            base = stored if isinstance(stored, SessionGoal) else goal
            attach_session_goal(
                session,
                base.with_status(SessionGoalStatus.ACTIVE).with_reason(reason),
            )
        return SessionGoalStatus.ACTIVE

    return _evaluate


__all__ = [
    "build_session_goal_llm_evaluator",
]
