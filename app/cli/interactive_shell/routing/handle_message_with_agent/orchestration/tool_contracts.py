"""Shared tool contracts and schema helpers for action orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.runtime.session import ReplSession

ToolExecutor = Callable[[dict[str, Any], "ToolContext"], bool]
ToolAvailability = Callable[[ReplSession], bool]
ToolSchema = dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    session: ReplSession
    console: Console
    confirm_fn: Callable[[str], str] | None = None
    is_tty: bool | None = None
    action_already_listed: bool = True


def _tool_is_available(_session: ReplSession) -> bool:
    return True


@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    input_schema: dict[str, Any]
    execution_tier: ExecutionTier
    execute: ToolExecutor
    is_available: ToolAvailability = _tool_is_available


def string_property(
    *,
    description: str,
    enum: tuple[str, ...] | None = None,
    min_length: int | None = None,
) -> ToolSchema:
    schema: ToolSchema = {"type": "string", "description": description}
    if enum:
        schema["enum"] = list(enum)
    if min_length is not None:
        schema["minLength"] = min_length
    return schema


def string_array_property(*, description: str) -> ToolSchema:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": description,
    }


def object_schema(*, properties: dict[str, ToolSchema], required: tuple[str, ...]) -> ToolSchema:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def capability_not_explicitly_disabled(session: ReplSession, capability_name: str) -> bool:
    available_capabilities = getattr(session, "available_capabilities", {})
    capability_values = (
        available_capabilities.get(capability_name)
        if isinstance(available_capabilities, dict)
        else None
    )
    return not (isinstance(capability_values, tuple) and capability_values == ())


__all__ = [
    "ToolAvailability",
    "ToolContext",
    "ToolEntry",
    "ToolExecutor",
    "ToolSchema",
    "capability_not_explicitly_disabled",
    "object_schema",
    "string_array_property",
    "string_property",
]
