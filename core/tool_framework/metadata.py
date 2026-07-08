"""Validated metadata schema for registered tools.

``ToolMetadata`` is the Pydantic model that enforces the tool contract at
class-definition time (via ``BaseTool.__init_subclass__``) and at
registration time (via ``RegisteredTool.__post_init__``). Keeping it here,
separate from the abstract base class, means the validation schema can
evolve independently of the dispatch protocol in ``BaseTool``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from config.strict_config import StrictConfigModel
from core.domain.types.evidence import EvidenceSource
from core.domain.types.retrieval import RetrievalControls

EvidenceType = Literal[
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
SideEffectLevel = Literal["none", "read_only", "mutating", "external"]


class ToolMetadata(StrictConfigModel):
    """Strict schema for tool metadata declared on BaseTool subclasses."""

    name: str
    description: str
    display_name: str | None = None
    input_schema: dict[str, Any]
    source: EvidenceSource
    source_id: str | None = None
    evidence_type: EvidenceType | None = None
    side_effect_level: SideEffectLevel | None = None
    use_cases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    anti_examples: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    injected_params: list[str] = Field(default_factory=list)
    retrieval_controls: RetrievalControls = Field(
        default_factory=RetrievalControls,
        description="Declares which structured retrieval controls this tool supports",
    )

    @field_validator("name", "description", "display_name")
    @classmethod
    def _require_non_empty_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be a non-empty string")
        return normalized


__all__ = ["EvidenceType", "SideEffectLevel", "ToolMetadata"]
