"""Shared tool contracts for the runtime loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from tools.registered_tool import RegisteredTool


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


@dataclass(frozen=True)
class AgentToolContext:
    """Resources available while a first-class agent tool executes."""

    resolved_integrations: dict[str, Any]
    resources: dict[str, Any] = field(default_factory=dict)
    _emit_update: Callable[[Any], None] | None = field(default=None, repr=False, compare=False)

    @property
    def on_update(self) -> Callable[[Any], None] | None:
        """Compatibility accessor for older AgentTool implementations."""
        return self._emit_update

    def emit_update(self, update: Any) -> None:
        """Publish a partial tool update to the runtime observer, if one is attached."""
        if self._emit_update is not None:
            self._emit_update(update)


# CodeQL currently misses PEP 695 ``type`` aliases in ``__all__`` export checks.
AgentToolExecutor: TypeAlias = Callable[[dict[str, Any], AgentToolContext], Any]  # noqa: UP040
ToolExecutionMode: TypeAlias = Literal["parallel", "sequential"]  # noqa: UP040


@dataclass(frozen=True)
class AgentTool:
    """Tool contract executed directly by the shared agent runtime."""

    name: str
    description: str
    input_schema: dict[str, Any]
    execute: AgentToolExecutor
    source: str = "agent"
    parallel_safe: bool = True
    execution_mode: ToolExecutionMode | None = None

    @property
    def effective_execution_mode(self) -> ToolExecutionMode:
        """Return the explicit execution policy, falling back to ``parallel_safe``."""
        if self.execution_mode is not None:
            return self.execution_mode
        return "parallel" if self.parallel_safe else "sequential"

    @property
    def public_input_schema(self) -> dict[str, Any]:
        return self.input_schema

    def validate_public_input(self, payload: dict[str, Any]) -> str | None:
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


# Keep this as an assignment-style alias for the same CodeQL export check.
RuntimeTool: TypeAlias = AgentTool | RegisteredTool  # noqa: UP040

__all__ = [
    "AgentTool",
    "AgentToolContext",
    "AgentToolExecutor",
    "RuntimeTool",
    "ToolExecutionMode",
]
