"""Shared tool contracts and schema helpers for tool orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from core.types import AgentTool, AgentToolContext
from interactive_shell.session import ReplSession

ToolExecutor = Callable[[dict[str, Any], "ToolContext"], bool]
ToolAvailability = Callable[[ReplSession], bool]
ToolSchema = dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    session: ReplSession
    console: Console
    confirm_fn: Callable[[str], str] | None = None
    is_tty: bool | None = None
    # Defaults False to match ``execution_allowed`` and the ``run_*`` helpers:
    # nothing has been listed yet, so the confirmation UX should show the action
    # summary. The tool-calling turn dispatcher (``run_tool_calling_turn``) passes
    # ``action_already_listed=True`` explicitly because it prints a numbered plan.
    action_already_listed: bool = False


def _tool_is_available(_session: ReplSession) -> bool:
    return True


@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    input_schema: dict[str, Any]
    execute: ToolExecutor
    # ``is_available`` gates BOTH planner offering and runtime dispatch.
    # ``is_planner_selectable`` additionally hides a tool from the planner's
    # tool specs WITHOUT blocking direct/programmatic dispatch, so a feature can
    # be removed from natural-language selection while staying reachable for
    # explicit, tested code paths.
    is_available: ToolAvailability = _tool_is_available
    is_planner_selectable: ToolAvailability = _tool_is_available

    @property
    def public_input_schema(self) -> dict[str, Any]:
        return self.input_schema

    def to_agent_tool(self, ctx: ToolContext) -> AgentTool:
        """Bind this shell tool to a REPL turn context."""

        def _execute(args: dict[str, Any], _agent_ctx: AgentToolContext) -> dict[str, Any]:
            if getattr(ctx.console, "cancel_requested", False):
                ctx.console.print("[dim](remaining actions cancelled)[/]")
                return {"ok": False, "cancelled": True}
            return {"ok": bool(self.execute(args, ctx))}

        return AgentTool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            execute=_execute,
            source="interactive_shell",
            parallel_safe=False,
        )


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
