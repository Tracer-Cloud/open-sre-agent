"""Action-surface helpers backed by the canonical tool registry."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from platform.observability.tool_trace import redact_sensitive
from tools.registered_tool import RegisteredTool
from tools.registry import get_registered_tool_map, get_registered_tools
from tools.utils.integration_sources import availability_view

from .contracts import ToolContext

_REPL_SESSION_SOURCE = "_repl_session"


def _sources_for_context(
    ctx: ToolContext,
    resolved_integrations: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    raw_sources = availability_view(resolved_integrations or {})
    sources = dict(raw_sources)
    sources[_REPL_SESSION_SOURCE] = {
        "session": ctx.session,
        "configured_integrations": tuple(ctx.session.configured_integrations),
        "configured_integrations_known": ctx.session.configured_integrations_known,
        "available_capabilities": getattr(ctx.session, "available_capabilities", {}),
    }
    return sources


def action_tools_for_context(
    ctx: ToolContext,
    *,
    resolved_integrations: dict[str, Any] | None = None,
) -> list[RegisteredTool]:
    """Return canonical registered tools available to the action agent."""
    sources = _sources_for_context(ctx, resolved_integrations)
    tools: list[RegisteredTool] = []
    for candidate in get_registered_tools("action"):
        try:
            if not candidate.is_available(sources):
                continue
        except Exception as exc:
            safe_sources = redact_sensitive(sources)
            raise RuntimeError(
                f"{candidate.name} availability check failed for sources {safe_sources!r}: {exc}"
            ) from exc
        tools.append(candidate)
    return tools


def get_action_tool(name: str) -> RegisteredTool | None:
    """Return a registered action tool by name."""
    return get_registered_tool_map("action").get(name)


def action_tool_names(tools: Iterable[RegisteredTool]) -> tuple[str, ...]:
    return tuple(tool.name for tool in tools)


__all__ = ["action_tool_names", "action_tools_for_context", "get_action_tool"]
