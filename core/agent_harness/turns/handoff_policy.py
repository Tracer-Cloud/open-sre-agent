"""Handoff-content policy predicates shared by gather skip and answer finalize.

Kept separate from routing (:mod:`turn_route`) and from stream/CTA finalize
(:mod:`answer_finalize`) so each seam stays small.
"""

from __future__ import annotations

from core.agent_harness.prompts.memory.prior_investigation import is_prior_investigation_follow_up
from core.agent_harness.turns.handoff_tag_parse import handoff_has_tag


def is_prior_investigation_follow_up_handoff(handoff_contents: tuple[str, ...]) -> bool:
    """True when the action planner handed off a session-prior-investigation follow-up."""
    return is_prior_investigation_follow_up(handoff_contents)


def is_non_investigation_handoff(handoff_contents: tuple[str, ...]) -> bool:
    """True for setup/query handoffs that must not force an investigate Want-me-to.

    ``finalize_gather_investigation_offer`` rewrites any existing Want-me-to
    closer to "run a full investigation". That is correct for diagnostic gather
    turns, but wrong for ``database_query:*`` / ``provider:*`` handoffs where the
    next step is connect/setup guidance.
    """
    return any(
        handoff_has_tag(content, "database_query") or handoff_has_tag(content, "provider")
        for content in handoff_contents
    )


__all__ = [
    "is_non_investigation_handoff",
    "is_prior_investigation_follow_up_handoff",
]
