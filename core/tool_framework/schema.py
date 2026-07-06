"""JSON schema inference and value-validation helpers for registered tools.

Inference: build a JSON Schema object from a Python function signature
(``infer_input_schema``) or a Pydantic model (``model_to_json_schema``).

Validation: check that a runtime value satisfies a JSON Schema fragment
(``_value_matches_schema``). Used by ``RegisteredTool.validate_public_input``
to reject bad planner payloads before they reach ``run()``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import NoneType, UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel

__all__ = [
    "infer_input_schema",
    "model_to_json_schema",
]


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
    elif origin is Union or isinstance(base_annotation, UnionType):  # noqa: UP007
        # Non-optional union (e.g. int | str): emit oneOf over each member.
        schema = {"oneOf": [_annotation_to_json_schema(a) for a in get_args(base_annotation)]}
    elif isinstance(base_annotation, tuple):
        # Residual tuple produced by _strip_optional for X | Y | None with >1 non-None arm.
        schema = {"oneOf": [_annotation_to_json_schema(a) for a in base_annotation]}
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
