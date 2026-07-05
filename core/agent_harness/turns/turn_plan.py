"""Turn-wide assembly: the decisions one turn runs on.

Assembled once at the top of ``run_turn`` and read by the action, gather, and
answer phases so they cannot disagree about what this turn knows. It composes the
frozen :class:`TurnSnapshot` (the read view of session state at turn start) and
exposes the turn's single resolved-integration view.

The snapshot answers "what did the session look like at turn start?"; the plan
answers "what is this turn running on?". Today the plan's decision is the
resolved integrations; tool lists and prompts stay built by their phases (action
tools need surface context; gather tools depend on message-time GitHub scope),
each reading ``resolved_integrations`` here so there is one source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent_harness.models.turn_snapshot import TurnSnapshot


@dataclass(frozen=True)
class TurnPlan:
    """Everything one turn runs on, assembled once at ``run_turn``."""

    snapshot: TurnSnapshot

    @property
    def text(self) -> str:
        """Raw user input text for this turn."""
        return self.snapshot.text

    @property
    def resolved_integrations(self) -> dict[str, Any]:
        """The turn's single resolved-integration view (frozen on the snapshot)."""
        return self.snapshot.resolved_integrations


def build_turn_plan(snapshot: TurnSnapshot) -> TurnPlan:
    """Assemble the turn plan from the resolved snapshot (the single owner)."""
    return TurnPlan(snapshot=snapshot)


__all__ = ["TurnPlan", "build_turn_plan"]
