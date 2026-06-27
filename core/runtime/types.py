"""Shared types for the Pi-style agent runtime layers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.runtime.llm.agent_llm_client import ToolCall
from tools.registered_tool import RegisteredTool

type AgentMessage = dict[str, Any]


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


type AgentToolExecutor = Callable[[dict[str, Any], AgentToolContext], Any]


@dataclass(frozen=True)
class AgentTool:
    """Tool contract executed directly by the shared agent runtime."""

    name: str
    description: str
    input_schema: dict[str, Any]
    execute: AgentToolExecutor
    source: str = "agent"
    parallel_safe: bool = True

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


type RuntimeTool = AgentTool | RegisteredTool


@dataclass
class AgentLoopResult:
    """Result returned by the pure think -> call tools -> observe loop."""

    messages: list[AgentMessage]
    final_text: str
    executed: list[tuple[ToolCall, Any]] = field(default_factory=list)
    hit_iteration_cap: bool = False


@dataclass
class AgentContext:
    """Runtime context consumed by an agent loop invocation."""

    llm: Any
    system_prompt: str
    messages: list[AgentMessage]
    tools: list[RuntimeTool]
    resolved_integrations: dict[str, Any]
    max_iterations: int


@dataclass
class AgentHarnessContext:
    """Snapshot used by harness providers to build one turn."""

    messages: list[AgentMessage]
    resolved_integrations: dict[str, Any]


class AgentSessionStore(Protocol):
    """Minimal persistence contract used by the harness layer."""

    def load_messages(self) -> list[AgentMessage]:
        """Return persisted agent messages for the active session."""

    def save_messages(self, messages: list[AgentMessage]) -> None:
        """Persist the complete message list after a turn."""


type SystemPromptProvider = str | Callable[[AgentHarnessContext], str]
type ToolProvider = Callable[[AgentHarnessContext], list[RuntimeTool]]
type IntegrationProvider = Callable[[], dict[str, Any]]
type LlmFactory = Callable[[], Any]

__all__ = [
    "AgentContext",
    "AgentHarnessContext",
    "AgentLoopResult",
    "AgentMessage",
    "AgentSessionStore",
    "AgentTool",
    "AgentToolContext",
    "AgentToolExecutor",
    "IntegrationProvider",
    "LlmFactory",
    "RuntimeTool",
    "SystemPromptProvider",
    "ToolProvider",
]
