"""Shared runtime tool definition for class-based and function-based tools."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from types import NoneType
from typing import Any, Literal, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from core.domain.types.evidence import EvidenceSource
from core.domain.types.retrieval import RetrievalControls
from core.domain.types.tools import ToolSurface
from tools.base import BaseTool, EvidenceType, SideEffectLevel, ToolMetadata

REGISTERED_TOOL_ATTR = "__opensre_registered_tool__"

_DEFAULT_SURFACES: tuple[ToolSurface, ...] = ("investigation",)
_VALID_SURFACES = set(get_args(ToolSurface))
CostTier = Literal["cheap", "moderate", "expensive"]
_VALID_COST_TIERS = set(get_args(CostTier))


def _always_available(_sources: dict[str, dict]) -> bool:
    return True


def _extract_no_params(_sources: dict[str, dict]) -> dict[str, Any]:
    return {}


def _normalize_surfaces(surfaces: Iterable[str] | None) -> tuple[ToolSurface, ...]:
    if surfaces is None:
        return _DEFAULT_SURFACES

    normalized: list[ToolSurface] = []
    for raw_surface in surfaces:
        surface = str(raw_surface).strip().lower()
        if surface not in _VALID_SURFACES:
            valid = ", ".join(sorted(_VALID_SURFACES))
            raise ValueError(f"Unsupported tool surface '{surface}'. Expected one of: {valid}.")
        typed_surface = cast(ToolSurface, surface)
        if typed_surface not in normalized:
            normalized.append(typed_surface)

    return tuple(normalized) or _DEFAULT_SURFACES


def _strip_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin is None:
        return annotation, False

    args = tuple(arg for arg in get_args(annotation) if arg is not NoneType)
    if len(args) != len(get_args(annotation)):
        if len(args) == 1:
            return args[0], True
        return args, True

    return annotation, False


def _annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    base_annotation, is_optional = _strip_optional(annotation)
    origin = get_origin(base_annotation)

    if base_annotation in (inspect.Signature.empty, Any):
        schema: dict[str, Any] = {}
    elif base_annotation is str:
        schema = {"type": "string"}
    elif base_annotation is int:
        schema = {"type": "integer"}
    elif base_annotation is float:
        schema = {"type": "number"}
    elif base_annotation is bool:
        schema = {"type": "boolean"}
    elif base_annotation is dict or origin is dict:
        schema = {"type": "object"}
    elif base_annotation is list or origin in (list, set, tuple):
        schema = {"type": "array"}
    else:
        schema = {"type": "string"}

    if is_optional:
        schema["nullable"] = True
    return schema


def infer_input_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Infer a minimal JSON schema from a function signature."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    type_hints = get_type_hints(func)

    for param in inspect.signature(func).parameters.values():
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        if param.name.startswith("_"):
            continue

        resolved_annotation = type_hints.get(param.name, param.annotation)
        schema = _annotation_to_json_schema(resolved_annotation)
        properties[param.name] = schema

        _, is_optional = _strip_optional(resolved_annotation)
        if param.default is inspect.Signature.empty and not is_optional:
            required.append(param.name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def model_to_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model to a JSON object schema for tools."""
    schema = model.model_json_schema()
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    schema.setdefault("type", "object")
    if schema.get("type") == "object":
        schema.setdefault("properties", {})
        schema.setdefault("required", [])
        schema.setdefault("additionalProperties", False)
    return schema


def _json_type_matches(value: Any, schema_type: str) -> bool:
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict)
    return True


def _value_matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    if value is None and bool(schema.get("nullable")):
        return True

    if "enum" in schema and value not in schema.get("enum", []):
        return False

    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        return any(
            isinstance(option, dict) and _value_matches_schema(value, option) for option in one_of
        )

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        return any(
            isinstance(option, dict) and _value_matches_schema(value, option) for option in any_of
        )

    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return _json_type_matches(value, schema_type)
    if isinstance(schema_type, list):
        return any(
            isinstance(item, str) and _json_type_matches(value, item) for item in schema_type
        )
    return True


@dataclass
class RegisteredTool:
    """Uniform runtime representation shared by all registered tools."""

    name: str
    description: str
    input_schema: dict[str, Any]
    source: EvidenceSource
    run: Callable[..., Any] = field(repr=False)
    display_name: str | None = None
    source_id: str | None = None
    evidence_type: EvidenceType | None = None
    side_effect_level: SideEffectLevel | None = None
    surfaces: tuple[ToolSurface, ...] = _DEFAULT_SURFACES
    use_cases: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    anti_examples: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    injected_params: tuple[str, ...] = ()
    retrieval_controls: RetrievalControls = field(
        default_factory=RetrievalControls,
    )
    is_available: Callable[[dict[str, dict]], bool] = field(
        default=_always_available,
        repr=False,
    )
    extract_params: Callable[[dict[str, dict]], dict[str, Any]] = field(
        default=_extract_no_params,
        repr=False,
    )
    tags: tuple[str, ...] = ()
    cost_tier: CostTier | None = None
    requires_approval: bool = False
    approval_reason: str = ""
    approval_expiry_seconds: int = 300
    approval_scope: str = "one_shot"
    origin_module: str = ""
    origin_name: str = ""

    def __post_init__(self) -> None:
        metadata = ToolMetadata.model_validate(
            {
                "name": self.name,
                "description": self.description,
                "display_name": self.display_name,
                "input_schema": self.input_schema,
                "source": self.source,
                "source_id": self.source_id,
                "evidence_type": self.evidence_type,
                "side_effect_level": self.side_effect_level,
                "use_cases": self.use_cases,
                "examples": self.examples,
                "anti_examples": self.anti_examples,
                "requires": self.requires,
                "outputs": self.outputs,
                "output_schema": self.output_schema,
                "injected_params": list(self.injected_params),
                "retrieval_controls": self.retrieval_controls,
            }
        )
        self.name = metadata.name
        self.description = metadata.description
        self.display_name = metadata.display_name
        self.input_schema = metadata.input_schema
        self.source = metadata.source
        self.source_id = metadata.source_id
        self.evidence_type = metadata.evidence_type
        self.side_effect_level = metadata.side_effect_level
        self.use_cases = metadata.use_cases
        self.examples = metadata.examples
        self.anti_examples = metadata.anti_examples
        self.requires = metadata.requires
        self.outputs = metadata.outputs
        self.output_schema = metadata.output_schema
        self.injected_params = tuple(metadata.injected_params)
        self.retrieval_controls = metadata.retrieval_controls
        self.surfaces = _normalize_surfaces(self.surfaces)
        if self.cost_tier is not None:
            normalized_cost_tier = self.cost_tier.strip().lower()
            if normalized_cost_tier not in _VALID_COST_TIERS:
                valid = ", ".join(sorted(_VALID_COST_TIERS))
                raise ValueError(
                    f"Unsupported cost tier '{self.cost_tier}'. Expected one of: {valid}."
                )
            self.cost_tier = cast(CostTier, normalized_cost_tier)

        if not callable(self.run):
            raise TypeError("run must be callable")
        if not callable(self.is_available):
            raise TypeError("is_available must be callable")
        if not callable(self.extract_params):
            raise TypeError("extract_params must be callable")

    @property
    def inputs(self) -> dict[str, str]:
        props = self.input_schema.get("properties", {})
        return {
            param: str(info.get("description", info.get("type", "")))
            for param, info in props.items()
        }

    @property
    def public_input_schema(self) -> dict[str, Any]:
        """Return a schema exposed to the model (without injected params)."""
        schema = deepcopy(self.input_schema)
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return schema
        for injected in self.injected_params:
            properties.pop(injected, None)
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [name for name in required if name not in self.injected_params]
        return schema

    def validate_public_input(self, payload: dict[str, Any]) -> str | None:
        """Validate model-provided input against this tool's public schema."""
        schema = self.public_input_schema
        if schema.get("type") != "object":
            return f"{self.name} exposes a non-object input schema."
        if not isinstance(payload, dict):
            return f"{self.name} expected object input."

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required")
        if not isinstance(required, list):
            required = []

        missing = [name for name in required if name not in payload]
        if missing:
            return f"{self.name} missing required args: {', '.join(sorted(missing))}."

        if schema.get("additionalProperties") is False:
            extra = sorted(name for name in payload if name not in properties)
            if extra:
                return f"{self.name} got unexpected args: {', '.join(extra)}."

        for key, value in payload.items():
            prop_schema = properties.get(key)
            if not isinstance(prop_schema, dict):
                continue
            if not _value_matches_schema(value, prop_schema):
                return f"{self.name}.{key} has invalid type/value."
        return None

    def __call__(self, **kwargs: Any) -> Any:
        try:
            return self.run(**kwargs)
        except Exception as exc:
            from platform.observability.sentry_sdk import capture_exception

            capture_exception(
                exc,
                context=f"tool.{self.name}",
                tags={"surface": "tool", "tool": self.name},
            )
            return {"error": str(exc), "exception_type": type(exc).__name__}

    @classmethod
    def from_base_tool(
        cls,
        tool: BaseTool,
        *,
        surfaces: Iterable[str] | None = None,
        retrieval_controls: RetrievalControls | None = None,
        tags: tuple[str, ...] | None = None,
        cost_tier: CostTier | None = None,
    ) -> RegisteredTool:
        metadata = tool.metadata()
        input_model = cast(type[BaseModel] | None, getattr(tool, "input_model", None))
        output_model = cast(type[BaseModel] | None, getattr(tool, "output_model", None))
        resolved_input_schema = (
            model_to_json_schema(input_model) if input_model else metadata.input_schema
        )
        resolved_output_schema = (
            model_to_json_schema(output_model) if output_model else metadata.output_schema
        )
        resolved_surfaces = (
            surfaces or getattr(tool, "surfaces", None) or getattr(tool.__class__, "surfaces", None)
        )
        resolved_tags = tuple(
            cast(
                Iterable[str],
                tags or getattr(tool, "tags", None) or getattr(tool.__class__, "tags", ()),
            )
        )
        resolved_cost_tier = cast(
            CostTier | None,
            cost_tier
            or getattr(tool, "cost_tier", None)
            or getattr(tool.__class__, "cost_tier", None),
        )
        return cls(
            name=metadata.name,
            description=metadata.description,
            display_name=metadata.display_name,
            input_schema=resolved_input_schema,
            source=metadata.source,
            source_id=metadata.source_id,
            evidence_type=metadata.evidence_type,
            side_effect_level=metadata.side_effect_level,
            use_cases=metadata.use_cases,
            examples=metadata.examples,
            anti_examples=metadata.anti_examples,
            requires=metadata.requires,
            outputs=metadata.outputs,
            output_schema=resolved_output_schema,
            injected_params=tuple(metadata.injected_params),
            retrieval_controls=retrieval_controls or metadata.retrieval_controls,
            surfaces=_normalize_surfaces(resolved_surfaces),
            run=tool.run,  # type: ignore[attr-defined]
            is_available=tool.is_available,
            extract_params=tool.extract_params,
            tags=resolved_tags,
            cost_tier=resolved_cost_tier,
            requires_approval=getattr(tool.__class__, "requires_approval", False),
            approval_reason=getattr(tool.__class__, "approval_reason", ""),
            approval_expiry_seconds=getattr(tool.__class__, "approval_expiry_seconds", 300),
            approval_scope=getattr(tool.__class__, "approval_scope", "one_shot"),
            origin_module=tool.__class__.__module__,
            origin_name=tool.__class__.__name__,
        )

    @classmethod
    def from_function(
        cls,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        input_schema: dict[str, Any] | None = None,
        input_model: type[BaseModel] | None = None,
        source: EvidenceSource | None,
        source_id: str | None = None,
        evidence_type: EvidenceType | None = None,
        side_effect_level: SideEffectLevel | None = None,
        surfaces: Iterable[str] | None = None,
        use_cases: list[str] | None = None,
        examples: list[str] | None = None,
        anti_examples: list[str] | None = None,
        requires: list[str] | None = None,
        outputs: dict[str, str] | None = None,
        output_schema: dict[str, Any] | None = None,
        output_model: type[BaseModel] | None = None,
        injected_params: tuple[str, ...] | None = None,
        retrieval_controls: RetrievalControls | None = None,
        is_available: Callable[[dict[str, dict]], bool] | None = None,
        extract_params: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
        tags: tuple[str, ...] | None = None,
        cost_tier: CostTier | None = None,
    ) -> RegisteredTool:
        if source is None:
            raise ValueError("Function tools must declare a source.")

        resolved_input_schema = (
            input_schema
            or (model_to_json_schema(input_model) if input_model is not None else None)
            or infer_input_schema(func)
        )
        resolved_output_schema = output_schema or (
            model_to_json_schema(output_model) if output_model is not None else None
        )
        inferred_description = inspect.getdoc(func) or func.__name__.replace("_", " ")
        return cls(
            name=name or func.__name__,
            description=description or inferred_description,
            display_name=display_name,
            input_schema=resolved_input_schema,
            source=source,
            source_id=source_id,
            evidence_type=evidence_type,
            side_effect_level=side_effect_level,
            surfaces=_normalize_surfaces(surfaces),
            use_cases=list(use_cases or []),
            examples=list(examples or []),
            anti_examples=list(anti_examples or []),
            requires=list(requires or []),
            outputs=dict(outputs or {}),
            output_schema=resolved_output_schema,
            injected_params=tuple(injected_params or ()),
            retrieval_controls=retrieval_controls or RetrievalControls(),
            run=func,
            is_available=is_available or _always_available,
            extract_params=extract_params or _extract_no_params,
            tags=tags or (),
            cost_tier=cost_tier,
            origin_module=func.__module__,
            origin_name=func.__name__,
        )
