"""Closed ``GOAL_REACHED`` / ``NOT_REACHED`` confirm via structured output.

Shared by the SessionGoal LLM confirm and the ReAct goal reviewer.
Uses a Pydantic ``Literal`` enum (JSON Schema ``enum``) — not free-text scrape.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.llm.shared.structured_output import StructuredOutputClient
from core.llm.types import AgentLLMClient

log = logging.getLogger(__name__)

ClosedVerdictName = Literal["GOAL_REACHED", "NOT_REACHED"]


class ClosedGoalVerdict(BaseModel):
    """Binary reached / not-reached for goal-review LLM calls."""

    verdict: ClosedVerdictName = Field(
        description=(
            "GOAL_REACHED when the goal is met; NOT_REACHED when required work was clearly not done"
        )
    )


class _AgentAsPromptClient:
    """Adapt :class:`AgentLLMClient` message ``invoke`` to prompt-string ``invoke``."""

    def __init__(self, llm: AgentLLMClient, *, system: str) -> None:
        self._llm = llm
        self._system = system

    def invoke(self, prompt: str) -> Any:
        return self._llm.invoke(
            [{"role": "user", "content": prompt}],
            system=self._system,
        )


def invoke_closed_goal_verdict(
    llm: AgentLLMClient,
    *,
    prompt: str,
    system: str,
) -> ClosedVerdictName | None:
    """Return the closed verdict, or ``None`` on transport / parse failure."""
    try:
        factory = getattr(llm, "with_structured_output", None)
        if callable(factory):
            parsed = factory(ClosedGoalVerdict).invoke(f"{system}\n\n{prompt}")
        else:
            parsed = StructuredOutputClient(
                _AgentAsPromptClient(llm, system=system),
                ClosedGoalVerdict,
            ).invoke(prompt)
    except Exception:
        log.debug("closed goal verdict LLM call failed", exc_info=True)
        return None

    if not isinstance(parsed, ClosedGoalVerdict):
        try:
            parsed = ClosedGoalVerdict.model_validate(parsed)
        except Exception:
            log.debug("closed goal verdict parse failed", exc_info=True)
            return None
    return parsed.verdict


__all__ = [
    "ClosedGoalVerdict",
    "ClosedVerdictName",
    "invoke_closed_goal_verdict",
]
