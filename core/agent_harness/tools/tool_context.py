"""Shared runtime context and schema helpers for action tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.tool.contracts import AgentToolContext

ToolExecutionPayload = bool | dict[str, Any]
ToolExecutor = Callable[[dict[str, Any], "ActionToolContext"], ToolExecutionPayload]
ACTION_TOOL_CONTEXT_RESOURCE_KEY = "action_tool_context"
_ACTION_SESSION_SOURCE = "_action_session"


@dataclass(frozen=True)
class ActionToolContext:
    """Per-turn resources exposed to action-surface tools."""

    session: Any
    console: Any
    confirm_fn: Callable[[str], str] | None = None
    is_tty: bool | None = None
    request_exit: Callable[[], None] | None = None
    # Defaults False to match ``execution_allowed`` and the ``run_*`` helpers:
    # nothing has been listed yet, so the confirmation UX should show the action
    # summary. The action-agent dispatcher passes True because it has already
    # rendered the planned action list.
    action_already_listed: bool = False
    #: Length of ``session.history`` when this turn began, so a tool can tell
    #: what THIS turn produced from what the session already contained.
    history_start: int = 0
    # Surface-injected subprocess presenter (``tools.interactive_shell.subprocess``).
    subprocess_presenter: Any = None
    investigation_ports: Any = None
    llm_provider_ports: Any = None
    task_cancel_ports: Any = None
    slash_ports: Any = None


def action_context_from_agent_context(context: AgentToolContext) -> ActionToolContext:
    action_context = context.resources.get(ACTION_TOOL_CONTEXT_RESOURCE_KEY)
    if not isinstance(action_context, ActionToolContext):
        raise RuntimeError("action tool requires action runtime context")
    return action_context


def execute_with_action_context(
    args: dict[str, Any],
    context: AgentToolContext,
    execute: ToolExecutor,
) -> dict[str, Any]:
    action_context = action_context_from_agent_context(context)
    if getattr(action_context.console, "cancel_requested", False):
        action_context.console.print("[dim](remaining actions cancelled)[/]")
        return {"ok": False, "cancelled": True}
    result = execute(args, action_context)
    if isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("ok", True)
        return payload
    return {"ok": bool(result)}


def capability_available_from_sources(
    sources: dict[str, dict[str, Any]],
    capability_name: str,
) -> bool:
    action_source = sources.get(_ACTION_SESSION_SOURCE) or {}
    available_capabilities = action_source.get("available_capabilities")
    capability_values = (
        available_capabilities.get(capability_name)
        if isinstance(available_capabilities, dict)
        else None
    )
    return not (isinstance(capability_values, tuple) and capability_values == ())


def capability_not_explicitly_disabled(session: Any, capability_name: str) -> bool:
    available_capabilities = getattr(session, "available_capabilities", {})
    capability_values = (
        available_capabilities.get(capability_name)
        if isinstance(available_capabilities, dict)
        else None
    )
    return not (isinstance(capability_values, tuple) and capability_values == ())


__all__ = [
    "ACTION_TOOL_CONTEXT_RESOURCE_KEY",
    "ActionToolContext",
    "ToolExecutor",
    "ToolExecutionPayload",
    "action_context_from_agent_context",
    "capability_available_from_sources",
    "capability_not_explicitly_disabled",
    "execute_with_action_context",
]
