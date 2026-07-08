"""Abstract base class for all investigation tool actions."""

from __future__ import annotations

from abc import ABC
from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic import BaseModel

from config.constants.investigation import DEFAULT_APPROVAL_EXPIRY_SECONDS
from core.domain.types.evidence import EvidenceSource
from core.domain.types.retrieval import RetrievalControls
from core.domain.types.tools import ToolSurface
from core.tool_framework.metadata import EvidenceType, SideEffectLevel, ToolMetadata
from core.tool_framework.registry_metadata import BaseToolRegistryMetadata


class BaseTool(ABC):
    """Abstract base class for every investigation tool.

    Subclass contract
    -----------------
    * Declare all metadata as **ClassVars** (``name``, ``description``,
      ``input_schema``, ``source``, etc.).  ``__init_subclass__`` validates
      them through ``ToolMetadata`` on class creation, so missing or
      ill-typed declarations fail at import time rather than at runtime.
    * Implement ``run(**kwargs)`` — *not* declared here to avoid forcing a
      fixed signature on every subclass.  The planner invokes the tool
      through ``__call__``, which delegates to ``run`` via
      ``telemetry.invoke_tool`` so exceptions are always captured and
      converted to a structured ``{"error": ..., "exception_type": ...}``
      dict rather than propagating to the agent loop.
    * Override ``is_available`` and ``extract_params`` when the tool
      requires specific data-source checks or needs to pull kwargs from the
      investigation sources dict.
    * Do **not** declare ``run`` with positional arguments — the call site
      always uses keyword arguments: ``tool_instance.run(**kwargs)``.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    display_name: ClassVar[str | None] = None
    input_schema: ClassVar[dict[str, Any]]  # JSON Schema — consumed by LLM planner
    input_model: ClassVar[type[BaseModel] | None] = None
    source: ClassVar[EvidenceSource]
    source_id: ClassVar[str | None] = None
    evidence_type: ClassVar[EvidenceType | None] = None
    side_effect_level: ClassVar[SideEffectLevel | None] = None
    use_cases: ClassVar[Sequence[str]] = ()
    examples: ClassVar[Sequence[str]] = ()
    anti_examples: ClassVar[Sequence[str]] = ()
    requires: ClassVar[Sequence[str]] = ()
    outputs: ClassVar[dict[str, str]] = {}  # Output field -> description (optional, for prompting)
    output_schema: ClassVar[dict[str, Any] | None] = None
    output_model: ClassVar[type[BaseModel] | None] = None
    injected_params: ClassVar[Sequence[str]] = ()
    retrieval_controls: ClassVar[RetrievalControls] = (
        RetrievalControls()
    )  # Declares supported controls
    surfaces: ClassVar[tuple[ToolSurface, ...]] = ("investigation",)
    tags: ClassVar[Sequence[str]] = ()
    parallel_safe: ClassVar[bool] = True
    requires_approval: ClassVar[bool] = False  # Whether this tool needs approval from messaging
    approval_reason: ClassVar[str] = ""  # Human-readable reason for requiring approval
    approval_expiry_seconds: ClassVar[int] = DEFAULT_APPROVAL_EXPIRY_SECONDS
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
        cls.use_cases = tuple(metadata.use_cases)
        cls.examples = tuple(metadata.examples)
        cls.anti_examples = tuple(metadata.anti_examples)
        cls.requires = tuple(metadata.requires)
        cls.outputs = metadata.outputs
        cls.output_schema = metadata.output_schema
        cls.injected_params = tuple(metadata.injected_params)
        cls.retrieval_controls = metadata.retrieval_controls
        registry = cls.registry_metadata()
        cls.surfaces = registry.surfaces
        cls.tags = registry.tags
        cls.parallel_safe = registry.parallel_safe

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

    @classmethod
    def registry_metadata(cls) -> BaseToolRegistryMetadata:
        """Return validated registry/runtime metadata for this subclass."""
        return BaseToolRegistryMetadata.model_validate(
            {
                "surfaces": getattr(cls, "surfaces", ("investigation",)),
                "tags": tuple(getattr(cls, "tags", ())),
                "parallel_safe": getattr(cls, "parallel_safe", True),
            }
        )

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        from core.tool_framework.telemetry import invoke_tool

        return invoke_tool(self.run, name=self.name, source=str(self.source), kwargs=kwargs)  # type: ignore[attr-defined, no-any-return]

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
