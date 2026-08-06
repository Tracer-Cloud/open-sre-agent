"""LLM goal reviewer for action and evidence-gather turns.

Builds a :class:`~core.agent.goals.Goal` whose ``verify`` asks the turn's own
LLM one small review question when the agent concludes after executing tools.
If the verdict is ``NOT_REACHED`` the ReAct loop nudges the agent to continue.
Two flavors share the same reviewer:

* :func:`build_goal_reviewer` — action turns ("remove the cron loops" must not
  stop after only listing them).
* :func:`build_gather_goal_reviewer` — evidence-gather turns (a data question
  must not stop at tool/schema discovery without executing the actual query;
  observed live: three PostHog turns in a row ended on MCP tool listings and
  never ran the count the user asked for).

The review is deliberately conservative — a wrong ``NOT_REACHED`` makes the
agent flail through extra actions the user never asked for (observed live:
duplicate investigation dispatches). It fails open on any LLM error, runs at
most once per turn, and is skipped entirely when no tools ran, when the agent
is asking the user a question, or when the turn ran a tool whose outcome is
not reviewable this turn (async investigation dispatch, assistant handoff).

The reviewer learns which tools ran through :func:`tap_executed_tool_names`,
a runtime-event wrapper the action driver installs around its event callback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.agent.goals import Goal, GoalObservation
from core.events import RuntimeEvent, RuntimeEventCallback, ToolExecutionEndEvent
from core.llm.types import AgentLLMClient, AgentLLMResponse

log = logging.getLogger(__name__)

# One rejection is enough to catch a stopped-short turn; the follow-up work is
# then accepted as-is. More reviews only amplify the damage when the reviewer
# itself is wrong, because every rejection burns loop iterations on nudges.
_MAX_GOAL_REVIEWS = 1

# Tools whose presence makes the turn's goal unreviewable at conclusion time:
# investigation dispatches are async (results arrive after the turn, so "not
# yet reached" is true but must not trigger a nudge), and a handoff means the
# conversational assistant owns the reply — second-guessing it as an
# unfinished *action* goal nudged live turns into unrequested investigations.
_SKIP_REVIEW_TOOL_NAMES = frozenset(
    {
        "alert_sample",
        "assistant_handoff",
        "investigation_start",
    }
)

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

_GATHER_REVIEW_SYSTEM_PROMPT = (
    "You review whether an evidence-gathering agent fetched the data needed to "
    "answer the user's question this turn.\n"
    "Reply with exactly one word: GOAL_REACHED or NOT_REACHED.\n"
    "Reply NOT_REACHED when the agent stopped at discovery or setup — e.g. it "
    "only listed available tools, fetched schemas, or described the query it "
    "would run — without executing that query and reporting the resulting "
    "data.\n"
    "A reply containing the requested data, or a clear statement that the "
    "connected data sources cannot provide it, counts as GOAL_REACHED. "
    "When in doubt, reply GOAL_REACHED."
)

_GATHER_SUCCESS_CRITERIA = (
    "The gathered results contain the actual data needed to answer the "
    "question (or a clear finding that it is unavailable) — tool listings and "
    "schema metadata are only preparation. You cannot ask the user anything "
    "during gathering: execute the query with the available tools."
)


def tap_executed_tool_names(
    inner: RuntimeEventCallback | None,
    names: list[str],
) -> RuntimeEventCallback:
    """Wrap ``inner`` to record each executed tool's name into ``names``."""

    def _callback(event: RuntimeEvent) -> None:
        if isinstance(event, ToolExecutionEndEvent):
            names.append(event.tool_name)
        if inner is not None:
            inner(event)

    return _callback


@dataclass
class _LLMGoalReviewer:
    """``Goal.verify`` predicate: one bounded, fail-open LLM review per turn."""

    llm: AgentLLMClient
    user_goal: str
    executed_tool_names: list[str]
    system_prompt: str = _REVIEW_SYSTEM_PROMPT
    skip_tool_names: frozenset[str] = _SKIP_REVIEW_TOOL_NAMES
    # Action turns skip review on a closing question: it seeks direction from
    # the user, and nudging would make the agent act without that answer. The
    # gather loop has no user to ask mid-pass, so its reviewer keeps going.
    skip_on_question: bool = True
    reviews_remaining: int = field(default=_MAX_GOAL_REVIEWS)

    def __call__(self, observation: GoalObservation) -> bool:
        final_text = (observation.final_text or "").strip()
        # No tools ran: the conclusion is a direct answer (or a refusal), not a
        # stopped-short action chain — the case this reviewer exists for.
        if observation.evidence_count == 0:
            return True
        if self.skip_on_question and final_text.endswith("?"):
            return True
        if any(name in self.skip_tool_names for name in self.executed_tool_names):
            return True
        if self.reviews_remaining <= 0:
            return True
        self.reviews_remaining -= 1
        try:
            response: AgentLLMResponse = self.llm.invoke(
                [{"role": "user", "content": self._review_message(observation)}],
                system=self.system_prompt,
            )
        except Exception:  # noqa: BLE001 - review is advisory; it must not fail the turn
            log.debug("goal review LLM call failed; accepting conclusion", exc_info=True)
            return True
        verdict = (response.content or "").strip().upper()
        return "NOT_REACHED" not in verdict

    def _review_message(self, observation: GoalObservation) -> str:
        final_text = (observation.final_text or "").strip() or "(empty)"
        return (
            f"User goal: {self.user_goal}\n"
            f"Actions executed this turn: {observation.evidence_count}\n"
            f"Agent's closing reply:\n{final_text}"
        )


def build_goal_reviewer(
    llm: AgentLLMClient,
    user_goal: str,
    executed_tool_names: list[str],
) -> Goal:
    """Build a reviewed :class:`Goal` for one action turn over ``user_goal``.

    ``executed_tool_names`` is the shared list a :func:`tap_executed_tool_names`
    wrapper fills as the turn runs; the reviewer reads it at conclusion time.
    """
    return Goal(
        description=user_goal,
        success_criteria=_GOAL_SUCCESS_CRITERIA,
        verify=_LLMGoalReviewer(
            llm=llm,
            user_goal=user_goal,
            executed_tool_names=executed_tool_names,
        ),
    )


def build_gather_goal_reviewer(llm: AgentLLMClient, user_goal: str) -> Goal:
    """Build a reviewed :class:`Goal` for one evidence-gather pass over ``user_goal``.

    Rejects conclusions that stopped at tool/schema discovery so the gather
    loop executes the actual query instead of returning listings the assistant
    can only apologize over. No skip-tool set (async dispatch and handoff tools
    are not on the gather surface) and no closing-question skip (there is no
    user to answer one mid-pass).
    """
    return Goal(
        description=f"Gather the live data needed to answer: {user_goal}",
        success_criteria=_GATHER_SUCCESS_CRITERIA,
        verify=_LLMGoalReviewer(
            llm=llm,
            user_goal=user_goal,
            executed_tool_names=[],
            system_prompt=_GATHER_REVIEW_SYSTEM_PROMPT,
            skip_tool_names=frozenset(),
            skip_on_question=False,
        ),
    )


__all__ = ["build_gather_goal_reviewer", "build_goal_reviewer", "tap_executed_tool_names"]
