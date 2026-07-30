"""Goal definition and verification for ReAct stop decisions.

Vincent M4 / M5-1D: do not conclude while the structured goal is unmet, but
always honour a hard iteration ceiling so the loop cannot spin forever.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GoalObservation:
    """What the loop knows when deciding whether the goal is met."""

    final_text: str
    evidence_count: int
    iteration: int
    max_iterations: int
    extras: dict[str, Any] | None = None


@dataclass(frozen=True)
class Goal:
    """A structured objective the agent must satisfy before stopping."""

    description: str
    success_criteria: str
    verify: Callable[[GoalObservation], bool] | None = None


def goal_met(goal: Goal, observation: GoalObservation) -> bool:
    """Return whether ``goal`` is satisfied given ``observation``.

    Custom ``goal.verify`` wins when provided. Otherwise require non-empty
    final text and at least one piece of evidence (tool result) — a cheap
    default that blocks empty “I’m done” conclusions.
    """
    if goal.verify is not None:
        return bool(goal.verify(observation))
    text = (observation.final_text or "").strip()
    return bool(text) and observation.evidence_count > 0


def should_accept_with_goal(
    goal: Goal | None,
    *,
    final_text: str,
    evidence_count: int,
    iteration: int,
    max_iterations: int,
    extras: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """Decide whether the ReAct loop may conclude.

    Returns ``(True, None)`` to accept, or ``(False, nudge)`` to continue.
    When ``iteration`` has reached the last allowed lap, accept even if the
    goal is unmet (budget ceiling).
    """
    if goal is None:
        return True, None
    # iteration is 0-based inside ReactLoop; last lap is max_iterations - 1.
    at_ceiling = iteration >= max(0, max_iterations - 1)
    observation = GoalObservation(
        final_text=final_text,
        evidence_count=evidence_count,
        iteration=iteration,
        max_iterations=max_iterations,
        extras=extras,
    )
    if goal_met(goal, observation):
        return True, None
    if at_ceiling:
        return True, None
    nudge = (
        f"Goal not yet met: {goal.description}. "
        f"Success criteria: {goal.success_criteria}. "
        "Continue gathering evidence or taking actions until the criteria are "
        "satisfied, then conclude with a clear answer."
    )
    return False, nudge


__all__ = [
    "Goal",
    "GoalObservation",
    "goal_met",
    "should_accept_with_goal",
]
