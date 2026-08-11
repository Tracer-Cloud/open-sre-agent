"""Typed ``assistant_handoff`` model — schema fields first, content tags as fallback.

The tool JSON Schema (closed ``evidence_kind`` enum, ``session_goal``,
``session_goal_items``) is the ontology. This dataclass is the in-process class
model. String tags in ``content`` are recovered only when structured fields are
empty, then lifted into fields so policy reads attributes — not a tag dialect.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.agent_harness.turns.evidence_kind import EvidenceKind
from core.agent_harness.turns.handoff_keys import HandoffField
from core.agent_harness.turns.handoff_tag_parse import find_tag_suffix, first_tag_token


def _evidence_kind_from_value(value: Any) -> EvidenceKind | None:
    if not isinstance(value, str):
        return None
    try:
        return EvidenceKind(value.strip().lower())
    except ValueError:
        return None


def _session_goal_attach(value: Any) -> bool | None:
    """Whether the planner asked to attach an session goal."""
    if isinstance(value, bool):
        return value
    return None


def _session_goal_from_content(content: str) -> bool | None:
    """Lift a legacy content tag into an attach flag (schema field preferred)."""
    if not content:
        return None
    body = find_tag_suffix(content, HandoffField.SESSION_GOAL)
    if body is None:
        return None
    # Progress tags are not attach.
    if body == "achieved" or body.startswith("done="):
        return None
    # ``session_goal=true`` / ``session_goal:continue`` / checklist labels.
    return True


def _session_goal_max_turns(value: Any) -> int | None:
    """Session-goal turn cap as a number; anything else is no cap."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(1, value)


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
    session_goal: bool | None = None
    session_goal_max_turns: int | None = None
    session_goal_items: tuple[str, ...] = ()
    requires_gather: bool = True

    @classmethod
    def from_tool_input(cls, handoff_input: Mapping[str, Any]) -> AssistantHandoff:
        """Decode tool args: schema fields win; content tags fill gaps only."""
        content = str(handoff_input.get(HandoffField.CONTENT, "")).strip()
        kind = _evidence_kind_from_value(handoff_input.get(HandoffField.EVIDENCE_KIND))
        if kind is None and content:
            kind = _evidence_kind_from_value(first_tag_token(content, HandoffField.EVIDENCE_KIND))

        session_goal = _session_goal_attach(handoff_input.get(HandoffField.SESSION_GOAL))
        max_turns = _session_goal_max_turns(handoff_input.get(HandoffField.SESSION_GOAL_MAX_TURNS))

        items = _session_goal_items_from_value(handoff_input.get(HandoffField.SESSION_GOAL_ITEMS))
        # A checklist is only meaningful under a goal, so items imply attach.
        # Otherwise a planner that sends items without the flag leaves a
        # half-state the host must guess at.
        if session_goal is None and items:
            session_goal = True
        if session_goal is None and content:
            session_goal = _session_goal_from_content(content)
        if not items and content:
            buried = find_tag_suffix(content, HandoffField.SESSION_GOAL_ITEM)
            if buried:
                items = (buried,)
                if session_goal is None:
                    session_goal = True
        # Metric counts need a number; omitting session_goal would drop the
        # host continuation loop after an incomplete gather/answer turn.
        # Explicit ``session_goal=false`` still opts out of attach.
        if session_goal is None and kind == EvidenceKind.METRIC_READ:
            session_goal = True

        requires_gather = handoff_input.get(HandoffField.REQUIRES_GATHER, True) is not False
        return cls(
            content=content,
            evidence_kind=kind,
            session_goal=session_goal,
            session_goal_max_turns=max_turns,
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
            tags.append(f"{HandoffField.EVIDENCE_KIND}:{self.evidence_kind.value}")
        if self.session_goal:
            tags.append(f"{HandoffField.SESSION_GOAL}:continue")
            if self.session_goal_max_turns is not None:
                tags.append(f"{HandoffField.SESSION_GOAL_MAX_TURNS}:{self.session_goal_max_turns}")
        for item in self.session_goal_items:
            tags.append(f"{HandoffField.SESSION_GOAL_ITEM}:{item}")
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
