"""Field and tag names the action planner uses to hand policy to the turn.

One spelling per concept. These names appear in three places that must agree:
the ``assistant_handoff`` tool schema, the decoder that reads a handoff, and
the guidance table keyed by tag prefix. A literal in any one of them drifts
silently, so every site names a member here instead.
"""

from __future__ import annotations

from enum import StrEnum


class HandoffField(StrEnum):
    """Keys in the ``assistant_handoff`` tool payload (and their inline tags)."""

    CONTENT = "content"
    EVIDENCE_KIND = "evidence_kind"
    SESSION_GOAL = "session_goal"
    SESSION_GOAL_MAX_TURNS = "session_goal_max_turns"
    SESSION_GOAL_ITEMS = "session_goal_items"
    SESSION_GOAL_ITEM = "session_goal_item"
    REQUIRES_GATHER = "requires_gather"


class HandoffTag(StrEnum):
    """Synthetic tags the harness appends for the answer path to read."""

    EVIDENCE_TIER = "evidence_tier"


__all__ = ["HandoffField", "HandoffTag"]
