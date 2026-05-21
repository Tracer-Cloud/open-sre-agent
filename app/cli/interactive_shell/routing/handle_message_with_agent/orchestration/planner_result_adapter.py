"""Single compatibility seam for legacy planner result shapes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)


@dataclass(frozen=True)
class PlannerResult:
    actions: list[PlannedAction]
    has_unhandled_clause: bool
    unavailable: bool


def normalize_planner_result(raw: Any) -> PlannerResult:
    """Normalize planner output to one stable runtime contract.

    Supported shapes:
    - ``None``: planner unavailable
    - ``(actions, has_unhandled_clause)``: legacy tuple form
    - ``PlannerResult``: canonical typed form
    """
    if raw is None:
        return PlannerResult(actions=[], has_unhandled_clause=True, unavailable=True)

    if isinstance(raw, PlannerResult):
        return raw

    if isinstance(raw, tuple) and len(raw) == 2:
        actions_raw, has_unhandled_raw = raw
        if not isinstance(actions_raw, list):
            msg = "Legacy planner tuple must contain a list of PlannedAction objects as item 0."
            raise TypeError(msg)
        return PlannerResult(
            actions=actions_raw,
            has_unhandled_clause=bool(has_unhandled_raw),
            unavailable=False,
        )

    msg = f"Unsupported planner result type: {type(raw).__name__}."
    raise TypeError(msg)


__all__ = ["PlannerResult", "normalize_planner_result"]
