"""Shared REPL action-tool context and schema helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from core.agent_harness.session import ReplSession
from core.types import AgentToolContext

ToolExecutor = Callable[[dict[str, Any], "ToolContext"], bool]
ToolSchema = dict[str, Any]
REPL_RESOURCE_KEY = "repl"


@dataclass(frozen=True)
class ToolContext:
    session: ReplSession
    console: Console
    confirm_fn: Callable[[str], str] | None = None
    is_tty: bool | None = None
    request_exit: Callable[[], None] | None = None
    # Defaults False to match ``execution_allowed`` and the ``run_*`` helpers:
    # nothing has been listed yet, so the confirmation UX should show the action
    # summary. The tool-calling turn dispatcher (``run_action_tool_turn``) passes
    # ``action_already_listed=True`` explicitly because it prints a numbered plan.
    action_already_listed: bool = False


def repl_context_from_agent_context(context: AgentToolContext) -> ToolContext:
    repl_context = context.resources.get(REPL_RESOURCE_KEY)
    if not isinstance(repl_context, ToolContext):
        raise RuntimeError("interactive shell action tool requires REPL runtime context")
    return repl_context


def execute_with_repl_context(
    args: dict[str, Any],
    context: AgentToolContext,
    execute: ToolExecutor,
) -> dict[str, Any]:
    repl_context = repl_context_from_agent_context(context)
    if getattr(repl_context.console, "cancel_requested", False):
        repl_context.console.print("[dim](remaining actions cancelled)[/]")
        return {"ok": False, "cancelled": True}
    return {"ok": bool(execute(args, repl_context))}


def capability_available_from_sources(
    sources: dict[str, dict[str, Any]],
    capability_name: str,
) -> bool:
    repl_source = sources.get("_repl_session") or {}
    available_capabilities = repl_source.get("available_capabilities")
    capability_values = (
        available_capabilities.get(capability_name)
        if isinstance(available_capabilities, dict)
        else None
    )
    return not (isinstance(capability_values, tuple) and capability_values == ())


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
    "ToolContext",
    "ToolExecutor",
    "ToolSchema",
    "REPL_RESOURCE_KEY",
    "capability_available_from_sources",
    "capability_not_explicitly_disabled",
    "execute_with_repl_context",
    "object_schema",
    "repl_context_from_agent_context",
    "string_array_property",
    "string_property",
]
