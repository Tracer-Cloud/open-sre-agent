"""Stable result shapes for the OpenClaw bridge tools.

Pure shaping of an MCP call result (or a failure reason) into the payload the
agent sees. Nothing here talks to the bridge — dispatch lives in ``__init__``.
"""

from __future__ import annotations

from core.tool_framework.utils import unavailable_response
from integrations.openclaw import OpenClawToolCallResult
from integrations.openclaw.tools.openclaw_mcp_tool.models import (
    OpenClawBridgeResponse,
    OpenClawConversationRow,
    OpenClawParams,
)

__all__ = ["conversation_rows_from_result", "normalize_tool_result", "unavailable_result"]

SOURCE = "openclaw"


def unavailable_result(
    error: str,
    *,
    tool_name: str | None = None,
    arguments: OpenClawParams | None = None,
) -> OpenClawBridgeResponse:
    """Degraded payload for any OpenClaw bridge call that could not complete."""
    return unavailable_response(SOURCE, error, tool_name=tool_name, arguments=arguments)


def normalize_tool_result(result: OpenClawToolCallResult) -> OpenClawBridgeResponse:
    """Shape a raw MCP tool-call result into a bridge response."""
    if result.get("is_error"):
        return unavailable_result(
            str(result.get("text") or "OpenClaw MCP tool call failed."),
            tool_name=str(result.get("tool", "")).strip() or None,
            arguments=result.get("arguments", {}),
        )
    return {
        "source": SOURCE,
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }


def conversation_rows_from_result(
    result: OpenClawToolCallResult,
) -> list[OpenClawConversationRow]:
    """Pull conversation rows out of a ``conversations_list`` result.

    The MCP side may return a bare list, a ``{"conversations": [...]}`` wrapper,
    or a single conversation dict; anything else yields no rows.
    """
    structured = result.get("structured_content")
    if isinstance(structured, list):
        return [item for item in structured if isinstance(item, dict)]
    if isinstance(structured, dict):
        conversations = structured.get("conversations")
        if isinstance(conversations, list):
            return [item for item in conversations if isinstance(item, dict)]
        return [structured]
    return []
