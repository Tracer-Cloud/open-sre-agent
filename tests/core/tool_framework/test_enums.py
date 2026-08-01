"""Pin the tool-metadata StrEnums to their wire string values.

These enums replace string ``Literal``s (analytics, frontmatter, and persisted
JSON all depend on the exact string values), so the members and their
round-trip from a plain string must stay stable. ``EvidenceSource`` is
deliberately *not* an enum — it is an open vendor-key registry — so it is not
covered here.
"""

from __future__ import annotations

import pytest

from core.domain.types.tools import ToolSurface
from core.tool_framework.metadata import (
    EvidenceType,
    SideEffectLevel,
    ToolMetadata,
)
from core.tool_framework.registry_metadata import normalize_surfaces

TOOL_SURFACE_VALUES = {
    ToolSurface.INVESTIGATION: "investigation",
    ToolSurface.CHAT: "chat",
    ToolSurface.ACTION: "action",
}
EVIDENCE_TYPE_VALUES = {
    EvidenceType.LOGS: "logs",
    EvidenceType.METRICS: "metrics",
    EvidenceType.TRACES: "traces",
    EvidenceType.EVENTS: "events",
    EvidenceType.TOPOLOGY: "topology",
    EvidenceType.DEPLOYMENT_METADATA: "deployment_metadata",
    EvidenceType.QUERY_STATS: "query_stats",
    EvidenceType.ARTIFACT: "artifact",
    EvidenceType.OTHER: "other",
}
SIDE_EFFECT_LEVEL_VALUES = {
    SideEffectLevel.NONE: "none",
    SideEffectLevel.READ_ONLY: "read_only",
    SideEffectLevel.MUTATING: "mutating",
    SideEffectLevel.EXTERNAL: "external",
}


@pytest.mark.parametrize(
    ("member", "value"),
    [
        *TOOL_SURFACE_VALUES.items(),
        *EVIDENCE_TYPE_VALUES.items(),
        *SIDE_EFFECT_LEVEL_VALUES.items(),
    ],
)
def test_member_value_is_stable(member: str, value: str) -> None:
    # StrEnum members compare equal to their wire string.
    assert member == value
    assert member.value == value
    # Round-trip: constructing from the string yields the same member.
    assert type(member)(value) is member


@pytest.mark.parametrize(
    ("enum", "expected"),
    [
        (ToolSurface, set(TOOL_SURFACE_VALUES.values())),
        (EvidenceType, set(EVIDENCE_TYPE_VALUES.values())),
        (SideEffectLevel, set(SIDE_EFFECT_LEVEL_VALUES.values())),
    ],
)
def test_enum_membership_is_closed(enum: type, expected: set[str]) -> None:
    assert {member.value for member in enum} == expected


def test_tool_metadata_coerces_strings_to_members() -> None:
    """Registry/decorator paths still accept the same plain strings."""
    meta = ToolMetadata.model_validate(
        {
            "name": "my_tool",
            "description": "Does something useful.",
            "input_schema": {"type": "object", "properties": {}},
            "source": "grafana",
            "evidence_type": "metrics",
            "side_effect_level": "read_only",
        }
    )
    assert meta.evidence_type is EvidenceType.METRICS
    assert meta.side_effect_level is SideEffectLevel.READ_ONLY
    # Persisted JSON keeps the original string shape.
    dumped = meta.model_dump(mode="json")
    assert dumped["evidence_type"] == "metrics"
    assert dumped["side_effect_level"] == "read_only"


def test_normalize_surfaces_accepts_strings_and_yields_members() -> None:
    assert normalize_surfaces(["chat", "action"]) == (
        ToolSurface.CHAT,
        ToolSurface.ACTION,
    )
    assert all(isinstance(s, ToolSurface) for s in normalize_surfaces(["investigation"]))


def test_normalize_surfaces_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported tool surface"):
        normalize_surfaces(["nope"])
