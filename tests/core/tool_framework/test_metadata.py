"""Unit tests for core.tool_framework.metadata (ToolMetadata contract)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from core.domain.types.retrieval import RetrievalControls
from core.domain.types.tools import ToolSurface
from core.tool_framework.metadata import EvidenceType, SideEffectLevel, ToolMetadata
from core.tool_framework.registered_tool import REGISTERED_TOOL_ATTR
from core.tool_framework.tool_decorator import tool


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "my_tool",
        "description": "Does something useful.",
        "input_schema": {"type": "object", "properties": {}},
        "source": "grafana",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Valid construction
# ---------------------------------------------------------------------------


def test_tool_metadata_valid_minimal() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs())
    assert meta.name == "my_tool"
    assert meta.description == "Does something useful."
    assert meta.source == "grafana"
    assert meta.display_name is None


def test_tool_metadata_strips_surrounding_whitespace() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(name="  padded  ", description="  desc  "))
    assert meta.name == "padded"
    assert meta.description == "desc"


def test_tool_metadata_display_name_none_is_accepted() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(display_name=None))
    assert meta.display_name is None


def test_tool_metadata_display_name_set() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(display_name="My Tool"))
    assert meta.display_name == "My Tool"


def test_tool_metadata_optional_lists_default_empty() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs())
    assert meta.use_cases == []
    assert meta.examples == []
    assert meta.anti_examples == []
    assert meta.requires == []
    assert meta.outputs == {}
    assert meta.injected_params == []


def test_tool_metadata_retrieval_controls_defaults_to_zero_value() -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs())
    assert isinstance(meta.retrieval_controls, RetrievalControls)


# ---------------------------------------------------------------------------
# Name / description validation
# ---------------------------------------------------------------------------


def test_tool_metadata_blank_name_rejected() -> None:
    with pytest.raises(ValidationError, match="name"):
        ToolMetadata.model_validate(_valid_kwargs(name="   "))


def test_tool_metadata_blank_description_rejected() -> None:
    with pytest.raises(ValidationError, match="description"):
        ToolMetadata.model_validate(_valid_kwargs(description=""))


def test_tool_metadata_blank_display_name_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolMetadata.model_validate(_valid_kwargs(display_name="  "))


# ---------------------------------------------------------------------------
# SideEffectLevel / EvidenceType literal validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["none", "read_only", "mutating", "external"])
def test_side_effect_level_valid_literals(level: str) -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(side_effect_level=level))
    assert meta.side_effect_level == level


def test_side_effect_level_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        ToolMetadata.model_validate(_valid_kwargs(side_effect_level="destructive"))


@pytest.mark.parametrize(
    "et",
    [
        "logs",
        "metrics",
        "traces",
        "events",
        "topology",
        "deployment_metadata",
        "query_stats",
        "artifact",
        "other",
    ],
)
def test_evidence_type_valid_literals(et: str) -> None:
    meta = ToolMetadata.model_validate(_valid_kwargs(evidence_type=et))
    assert meta.evidence_type == et


def test_evidence_type_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        ToolMetadata.model_validate(_valid_kwargs(evidence_type="profiling"))


# ---------------------------------------------------------------------------
# StrEnum contract: wire values, str-compat, and plain-string coercion
# ---------------------------------------------------------------------------


def test_enums_are_str_subclasses() -> None:
    assert issubclass(EvidenceType, str)
    assert issubclass(SideEffectLevel, str)
    assert issubclass(ToolSurface, str)


def test_evidence_type_wire_values_are_stable() -> None:
    assert [member.value for member in EvidenceType] == [
        "logs",
        "metrics",
        "traces",
        "events",
        "topology",
        "deployment_metadata",
        "query_stats",
        "artifact",
        "other",
    ]


def test_side_effect_level_wire_values_are_stable() -> None:
    assert [member.value for member in SideEffectLevel] == [
        "none",
        "read_only",
        "mutating",
        "external",
    ]


def test_tool_surface_wire_values_are_stable() -> None:
    assert [member.value for member in ToolSurface] == ["investigation", "chat", "action"]


def test_enum_members_compare_and_serialize_as_plain_strings() -> None:
    assert EvidenceType.LOGS == "logs"
    assert SideEffectLevel.READ_ONLY == "read_only"
    assert ToolSurface.INVESTIGATION == "investigation"
    # f-strings / JSON keep emitting the bare value, not "EvidenceType.LOGS".
    assert f"{EvidenceType.LOGS}" == "logs"
    assert json.dumps({"s": ToolSurface.CHAT}) == '{"s": "chat"}'


def test_enum_round_trips_from_string() -> None:
    assert EvidenceType("metrics") is EvidenceType.METRICS
    assert SideEffectLevel("mutating") is SideEffectLevel.MUTATING
    assert ToolSurface("action") is ToolSurface.ACTION


def test_metadata_coerces_plain_strings_to_enum_members() -> None:
    meta = ToolMetadata.model_validate(
        _valid_kwargs(evidence_type="logs", side_effect_level="read_only")
    )
    assert meta.evidence_type is EvidenceType.LOGS
    assert meta.side_effect_level is SideEffectLevel.READ_ONLY


def test_decorator_accepts_plain_strings_and_stores_enum_members() -> None:
    @tool(
        name="coercion_probe",
        description="Probe that plain strings coerce to enum members.",
        source="grafana",
        evidence_type="metrics",
        side_effect_level="read_only",
        surfaces=("investigation", "chat"),
    )
    def _probe() -> dict[str, str]:
        return {}

    registered = getattr(_probe, REGISTERED_TOOL_ATTR)
    assert registered.evidence_type is EvidenceType.METRICS
    assert registered.side_effect_level is SideEffectLevel.READ_ONLY
    assert registered.surfaces == (ToolSurface.INVESTIGATION, ToolSurface.CHAT)
    assert all(isinstance(surface, ToolSurface) for surface in registered.surfaces)
