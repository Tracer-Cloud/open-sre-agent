"""Typed ``assistant_handoff`` model — schema fields first, content tags as fallback.

The tool JSON Schema (closed ``evidence_kind`` enum, ``session_goal``,
``session_goal_items``) is the ontology. This dataclass is the in-process class
model. String tags in ``content`` are recovered only when structured fields are
empty, then lifted into fields so policy reads attributes — not a tag dialect.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.agent_harness.turns.handoff_tag_parse import find_tag_suffix, first_tag_token

if TYPE_CHECKING:
    from core.agent_harness.turns.evidence_need import EvidenceKind

_EVIDENCE_KIND_VALUES = frozenset({"metric_read", "incident", "setup", "other"})


def _evidence_kind_from_value(value: Any) -> EvidenceKind | None:
    from core.agent_harness.turns.evidence_need import EvidenceKind

    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    if token not in _EVIDENCE_KIND_VALUES:
        return None
    return EvidenceKind(token)


def _session_goal_body(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    body = value.strip()
    if not body or body == "achieved" or body.startswith("done="):
        return None
    return body


def _session_goal_items_from_value(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                items.append(text)
    return tuple(items)


@dataclass(frozen=True, slots=True)
class AssistantHandoff:
    """One planner ``assistant_handoff`` tool call, decoded for harness policy."""

    content: str = ""
    evidence_kind: EvidenceKind | None = None
    session_goal: str | None = None
    session_goal_items: tuple[str, ...] = ()
    requires_gather: bool = True

    @classmethod
    def from_tool_input(cls, handoff_input: Mapping[str, Any]) -> AssistantHandoff:
        """Decode tool args: schema fields win; content tags fill gaps only."""
        content = str(handoff_input.get("content", "")).strip()
        kind = _evidence_kind_from_value(handoff_input.get("evidence_kind"))
        if kind is None and content:
            kind = _evidence_kind_from_value(first_tag_token(content, "evidence_kind"))

        session_goal = _session_goal_body(handoff_input.get("session_goal"))
        if session_goal is None and content:
            session_goal = _session_goal_body(find_tag_suffix(content, "session_goal"))

        items = _session_goal_items_from_value(handoff_input.get("session_goal_items"))
        if not items and content:
            buried = find_tag_suffix(content, "session_goal_item")
            if buried:
                items = (buried,)

        requires_gather = handoff_input.get("requires_gather", True) is not False
        return cls(
            content=content,
            evidence_kind=kind,
            session_goal=session_goal,
            session_goal_items=items,
            requires_gather=requires_gather,
        )

    def to_handoff_contents(self) -> tuple[str, ...]:
        """Serialize to legacy tag strings for callers that still key off tags.

        Emits clean ``key:value`` forms (colon) so downstream tag readers stay
        stable. Prefer reading :class:`AssistantHandoff` fields for new policy.
        """
        tags: list[str] = []
        if self.content:
            tags.append(self.content)
        if self.evidence_kind is not None:
            tags.append(f"evidence_kind:{self.evidence_kind.value}")
        if self.session_goal is not None:
            tags.append(f"session_goal:{self.session_goal}")
        for item in self.session_goal_items:
            tags.append(f"session_goal_item:{item}")
        return tuple(tags)


def assistant_handoffs_from_tool_inputs(
    handoff_inputs: Sequence[Mapping[str, Any]],
) -> tuple[AssistantHandoff, ...]:
    """Decode every ``assistant_handoff`` tool input for a turn."""
    return tuple(AssistantHandoff.from_tool_input(raw) for raw in handoff_inputs)


def evidence_kind_from_assistant_handoffs(
    handoffs: Sequence[AssistantHandoff],
) -> EvidenceKind | None:
    """First structured ``evidence_kind`` on the turn's handoffs."""
    for handoff in handoffs:
        if handoff.evidence_kind is not None:
            return handoff.evidence_kind
    return None


__all__ = [
    "AssistantHandoff",
    "assistant_handoffs_from_tool_inputs",
    "evidence_kind_from_assistant_handoffs",
]
