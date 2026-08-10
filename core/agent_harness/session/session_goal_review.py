"""Optional LLM confirm for outer SessionGoal after structured evidence passes.

Structured :func:`~core.agent_harness.session.session_goal_evaluate.evaluate_session_goal`
is the default host judge. This module adds a second, independent model check
only when that judge would achieve a **condition-only** goal via the achieved
tag + tool evidence path.

Checklist completion stays structured-only (done indices are already host-tracked).
Fails open to ACTIVE on LLM errors so a broken reviewer cannot false-complete.
"""

from __future__ import annotations

import logging
from typing import Any

from core.agent_harness.session.session_goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
)
from core.agent_harness.session.session_goal_evaluate import evaluate_session_goal
from core.llm.types import AgentLLMClient

log = logging.getLogger(__name__)

_REVIEW_SYSTEM = (
    "You review whether an outer multi-turn session goal is met.\n"
    "Reply with exactly one word: GOAL_REACHED or NOT_REACHED.\n"
    "Reply GOAL_REACHED only when the assistant reply plus tools clearly "
    "satisfy the goal condition. When in doubt, reply NOT_REACHED."
)


# The reviewer only needs the closing reply to judge the condition; a longer
# tail costs tokens on every outer turn without changing the verdict.
MAX_REVIEWED_REPLY_CHARS = 4000


def _llm_confirms_achieved(
    llm: AgentLLMClient,
    *,
    condition: str,
    reply: str,
) -> bool | None:
    """Return True/False from the model, or None on error / unclear."""
    user = (
        f"Goal condition:\n{condition}\n\n"
        f"Latest assistant reply:\n{reply[:MAX_REVIEWED_REPLY_CHARS]}\n\n"
        "Is the goal reached?"
    )
    try:
        response = llm.invoke(
            [{"role": "user", "content": user}],
            system=_REVIEW_SYSTEM,
        )
    except Exception:
        log.debug("session_goal LLM confirm failed", exc_info=True)
        return None
    content = (getattr(response, "content", None) or str(response) or "").strip().upper()
    if "NOT_REACHED" in content:
        return False
    if "GOAL_REACHED" in content:
        return True
    return None


def build_session_goal_llm_evaluator(llm: AgentLLMClient):
    """Return an ``evaluate(goal, result, *, session=) -> status`` for the outer loop.

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
            or verdict.reason != "achieved with tool evidence"
        ):
            return verdict.status

        reply = ""
        text = getattr(result, "assistant_response_text", None)
        if isinstance(text, str):
            reply = text
        elif hasattr(result, "primary_response_text"):
            reply = str(result.primary_response_text or "")

        confirmed = _llm_confirms_achieved(llm, condition=goal.condition, reply=reply)
        if confirmed is True:
            return SessionGoalStatus.ACHIEVED
        # False or None → keep working (never false-complete on reviewer failure).
        reason = (
            "LLM confirm: not reached"
            if confirmed is False
            else "LLM confirm unavailable; staying active"
        )
        if session is not None:
            stored = getattr(session, "session_goal", None)
            if isinstance(stored, SessionGoal):
                attach_session_goal(session, stored.with_reason(reason))
            else:
                attach_session_goal(session, goal.with_reason(reason))
        return SessionGoalStatus.ACTIVE

    return _evaluate


__all__ = ["build_session_goal_llm_evaluator"]
