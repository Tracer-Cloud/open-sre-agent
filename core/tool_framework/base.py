"""Abstract base class for all investigation tool actions."""

from __future__ import annotations

from abc import ABC
from typing import Any, ClassVar

from pydantic import BaseModel

from core.domain.types.evidence import EvidenceSource
from core.domain.types.retrieval import RetrievalControls
from core.tool_framework.metadata import EvidenceType, SideEffectLevel, ToolMetadata


class BaseTool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    display_name: ClassVar[str | None] = None
    input_schema: ClassVar[dict[str, Any]]  # JSON Schema — consumed by LLM planner
    input_model: ClassVar[type[BaseModel] | None] = None
    source: ClassVar[EvidenceSource]
    source_id: ClassVar[str | None] = None
    evidence_type: ClassVar[EvidenceType | None] = None
    side_effect_level: ClassVar[SideEffectLevel | None] = None
    use_cases: ClassVar[list[str]] = []
    examples: ClassVar[list[str]] = []
    anti_examples: ClassVar[list[str]] = []
    requires: ClassVar[list[str]] = []
    outputs: ClassVar[dict[str, str]] = {}  # Output field -> description (optional, for prompting)
    output_schema: ClassVar[dict[str, Any] | None] = None
    output_model: ClassVar[type[BaseModel] | None] = None
    injected_params: ClassVar[list[str]] = []
    retrieval_controls: ClassVar[RetrievalControls] = (
        RetrievalControls()
    )  # Declares supported controls
    requires_approval: ClassVar[bool] = False  # Whether this tool needs approval from messaging
    approval_reason: ClassVar[str] = ""  # Human-readable reason for requiring approval
    approval_expiry_seconds: ClassVar[int] = (
        300  # Approval auto-expires after N seconds (default 5 min)
    )
    accepts_runtime_context: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        metadata = cls.metadata()
        cls.name = metadata.name
        cls.description = metadata.description
        cls.display_name = metadata.display_name
        cls.input_schema = metadata.input_schema
        cls.source = metadata.source
        cls.source_id = metadata.source_id
        cls.evidence_type = metadata.evidence_type
        cls.side_effect_level = metadata.side_effect_level
        cls.use_cases = metadata.use_cases
        cls.examples = metadata.examples
        cls.anti_examples = metadata.anti_examples
        cls.requires = metadata.requires
        cls.outputs = metadata.outputs
        cls.output_schema = metadata.output_schema
        cls.injected_params = metadata.injected_params
        cls.retrieval_controls = metadata.retrieval_controls

    @classmethod
    def metadata(cls) -> ToolMetadata:
        """Return validated tool metadata for this subclass."""
        return ToolMetadata.model_validate(
            {
                "name": getattr(cls, "name", ""),
                "description": getattr(cls, "description", ""),
                "display_name": getattr(cls, "display_name", None),
                "input_schema": getattr(cls, "input_schema", {}),
                "source_id": getattr(cls, "source_id", None),
                "source": getattr(cls, "source", ""),
                "evidence_type": getattr(cls, "evidence_type", None),
                "side_effect_level": getattr(cls, "side_effect_level", None),
                "use_cases": list(getattr(cls, "use_cases", [])),
                "examples": list(getattr(cls, "examples", [])),
                "anti_examples": list(getattr(cls, "anti_examples", [])),
                "requires": list(getattr(cls, "requires", [])),
                "outputs": dict(getattr(cls, "outputs", {})),
                "output_schema": getattr(cls, "output_schema", None),
                "injected_params": list(getattr(cls, "injected_params", [])),
                "retrieval_controls": getattr(cls, "retrieval_controls", RetrievalControls()),
            }
        )

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        from core.tool_framework.telemetry import invoke_tool

        return invoke_tool(self.run, name=self.name, source=str(self.source), kwargs=kwargs)  # type: ignore[attr-defined]

    def is_available(self, _sources: dict[str, dict]) -> bool:
        """Return True when required data sources are present.

        Override per tool. Default allows the tool to always run.
        """
        return True

    def extract_params(self, _sources: dict[str, dict]) -> dict[str, Any]:
        """Extract the kwargs to pass to ``run()`` from the available sources.

        Override per tool. Default returns an empty dict.
        """
        return {}
