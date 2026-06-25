"""Finalization for planner action results.

The LLM action planner is the sole tool selector: there is no regex-based
intent inference and no deterministic post-hoc rewriting of the model's chosen
actions. This module therefore only wraps the parsed plan in a stable result
type; it does not second-guess the planner.
"""

from __future__ import annotations

from typing import Any

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)


class PlannerPolicyResult:
    """Finalized planner output with an explicit (now always empty) policy trace."""

    __slots__ = ("actions", "has_unhandled", "applied_policies")

    def __init__(
        self,
        actions: list[PlannedAction],
        has_unhandled: bool,
        applied_policies: tuple[str, ...] = (),
    ) -> None:
        self.actions = actions
        self.has_unhandled = has_unhandled
        self.applied_policies = applied_policies


def finalize_planner_result_with_trace(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
    *,
    session: Any | None = None,
) -> PlannerPolicyResult:
    """Return the planner's actions unchanged.

    Kept as a thin seam so callers have one place to finalize a plan; it no
    longer applies any deterministic overrides.
    """
    del message, session
    return PlannerPolicyResult(actions, has_unhandled)


__all__ = [
    "PlannerPolicyResult",
    "finalize_planner_result_with_trace",
]
