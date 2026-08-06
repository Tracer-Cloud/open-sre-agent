"""LLM goal reviewer for action turns: did the agent reach the user's goal?

Builds a :class:`~core.agent.goals.Goal` whose ``verify`` asks the turn's own
LLM one small review question when the agent concludes after executing tools.
If the verdict is ``NOT_REACHED`` the ReAct loop nudges the agent to continue
(e.g. "remove the cron loops" must not stop after only listing them).

The review is deliberately conservative — a wrong ``NOT_REACHED`` makes the
agent flail through extra actions the user never asked for (observed live:
duplicate investigation dispatches). It fails open on any LLM error, runs at
most once per turn, and is skipped entirely when the turn dispatched an
investigation (async by design: the dispatch *is* the turn's goal), when no
tools ran, or when the agent is asking the user a question.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.agent.goals import Goal, GoalObservation
from core.agent_harness.ports import SessionStore
from core.llm.types import AgentLLMClient

log = logging.getLogger(__name__)

# One rejection is enough to catch a stopped-short turn; the follow-up work is
# then accepted as-is. More reviews only amplify the damage when the reviewer
# itself is wrong, because every rejection burns loop iterations on nudges.
_MAX_GOAL_REVIEWS = 1

# History row types written by async dispatch tools (investigation_start /
# alert_sample record type "alert"). Their results arrive after the turn, so
# "the goal is not yet reached" is true but must not trigger a nudge.
_ASYNC_DISPATCH_HISTORY_TYPES = frozenset({"alert"})

_REVIEW_SYSTEM_PROMPT = (
    "You review whether an agent completed the user's goal this turn.\n"
    "Reply with exactly one word: GOAL_REACHED or NOT_REACHED.\n"
    "Reply NOT_REACHED only when the goal clearly required actions the agent "
    "did not take — e.g. the user asked to change, create, or remove something "
    "and the agent only looked it up.\n"
    "An honest report of findings, an answer to a question, or a statement "
    "that there is nothing to act on all count as GOAL_REACHED. "
    "When in doubt, reply GOAL_REACHED."
)

_GOAL_SUCCESS_CRITERIA = (
    "The user's request has been fully carried out, not merely inspected or partially done."
)


@dataclass
class _LLMGoalReviewer:
    """``Goal.verify`` predicate: one bounded, fail-open LLM review per turn."""

    llm: AgentLLMClient
    user_goal: str
    session: SessionStore
    history_start: int
    reviews_remaining: int = field(default=_MAX_GOAL_REVIEWS)

    def __call__(self, observation: GoalObservation) -> bool:
        final_text = (observation.final_text or "").strip()
        # No tools ran: the conclusion is a direct answer (or a refusal), not a
        # stopped-short action chain — the case this reviewer exists for.
        if observation.evidence_count == 0:
            return True
        # A closing question seeks direction from the user; nudging the agent
        # to "continue" would make it act without the answer it just asked for.
        if final_text.endswith("?"):
            return True
        if self._dispatched_async_work():
            return True
        if self.reviews_remaining <= 0:
            return True
        self.reviews_remaining -= 1
        try:
            response = self.llm.invoke(
                [{"role": "user", "content": self._review_message(observation)}],
                system=_REVIEW_SYSTEM_PROMPT,
            )
        except Exception:
            log.debug("goal review LLM call failed; accepting conclusion", exc_info=True)
            return True
        verdict = (response.content or "").strip().upper()
        return "NOT_REACHED" not in verdict

    def _dispatched_async_work(self) -> bool:
        """True when this turn dispatched an investigation (results come later)."""
        return any(
            row.get("type") in _ASYNC_DISPATCH_HISTORY_TYPES
            for row in self.session.history[self.history_start :]
        )

    def _review_message(self, observation: GoalObservation) -> str:
        final_text = (observation.final_text or "").strip() or "(empty)"
        return (
            f"User goal: {self.user_goal}\n"
            f"Actions executed this turn: {observation.evidence_count}\n"
            f"Agent's closing reply:\n{final_text}"
        )


def build_goal_reviewer(llm: AgentLLMClient, user_goal: str, session: SessionStore) -> Goal:
    """Build a reviewed :class:`Goal` for one action turn over ``user_goal``.

    Call at turn start: the current history length marks where this turn's
    rows begin, so the reviewer can see what the turn itself dispatched.
    """
    return Goal(
        description=user_goal,
        success_criteria=_GOAL_SUCCESS_CRITERIA,
        verify=_LLMGoalReviewer(
            llm=llm,
            user_goal=user_goal,
            session=session,
            history_start=len(session.history),
        ),
    )


__all__ = ["build_goal_reviewer"]
