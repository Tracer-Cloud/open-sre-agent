"""Turn-wide assembly: the decisions one turn runs on.

Assembled once at the top of ``run_turn`` and read by the action, gather, and
answer phases so they cannot disagree about what this turn knows. It composes the
frozen :class:`TurnSnapshot` (the read view of session state at turn start) with
the turn's resolved-integration decision.

The snapshot answers "what did the session look like at turn start?"; the plan
answers "what is this turn running on?". ``build_turn_plan`` owns the assembly:
it resolves integrations once and composes them into the snapshot. Tool lists and
prompts stay built by their phases (action tools need surface context; gather
tools depend on message-time GitHub scope), each reading ``resolved_integrations``
here so there is one source.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from core.agent_harness.integrations.resolution import resolve_and_cache_integrations
from core.agent_harness.models.turn_snapshot import TurnSnapshot
from core.agent_harness.ports import SessionStore


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


def build_turn_plan(snapshot: TurnSnapshot, session: SessionStore) -> TurnPlan:
    """Assemble the turn plan: resolve integrations once, then compose the snapshot.

    Resolution runs only when the snapshot has not already been populated (a
    runtime-request source can pre-fill it), so the plan is the single place that
    decides what this turn knows about connected integrations.

    An empty result (``{}`` — no integrations configured) is a valid resolved
    view; downstream phases read it from the plan rather than re-checking, so the
    resolve-once contract holds even in that case (``resolve_and_cache`` also
    caches, so a repeat call would be a no-op regardless).
    """
    if not snapshot.resolved_integrations:
        snapshot = replace(snapshot, resolved_integrations=resolve_and_cache_integrations(session))
    return TurnPlan(snapshot=snapshot)


__all__ = ["TurnPlan", "build_turn_plan"]
