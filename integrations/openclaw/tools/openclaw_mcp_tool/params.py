"""Read OpenClaw connection settings off the verified agent-state source.

These back the ``is_available`` / ``extract_params`` hooks of the bridge tools:
pure dict reads over the ``openclaw`` source, with no bridge traffic.
"""

from __future__ import annotations

from core.tool_framework.utils.mcp_params import first_list, first_string
from integrations.openclaw.tools.openclaw_mcp_tool.models import OpenClawParams

__all__ = [
    "conversation_detail_params",
    "conversation_search_params",
    "extract_params",
    "is_available",
]

SEARCH_RESULT_LIMIT = 10


def is_available(sources: dict[str, dict]) -> bool:
    """True only once the OpenClaw integration has a verified connection."""
    return bool(sources.get("openclaw", {}).get("connection_verified"))


def extract_params(sources: dict[str, dict]) -> OpenClawParams:
    """Connection overrides for a bridge call, read from the OpenClaw source.

    Reads both the prefixed catalog keys (``openclaw_url``) and the short
    runtime aliases produced by ``OpenClawConfig.model_dump()`` (``url``).
    """
    openclaw = sources.get("openclaw", {})
    if not openclaw:
        return {}
    return {
        "openclaw_url": first_string(openclaw, "openclaw_url", "url"),
        "openclaw_mode": first_string(openclaw, "openclaw_mode", "mode"),
        "openclaw_token": first_string(openclaw, "openclaw_token", "auth_token"),
        "openclaw_command": first_string(openclaw, "openclaw_command", "command"),
        "openclaw_args": first_list(openclaw, "openclaw_args", "args"),
    }


def conversation_id(sources: dict[str, dict]) -> str:
    """Conversation id carried on the OpenClaw source, or an empty string."""
    openclaw = sources.get("openclaw", {})
    return str(
        openclaw.get("openclaw_conversation_id") or openclaw.get("conversation_id") or ""
    ).strip()


def conversation_search_params(sources: dict[str, dict]) -> OpenClawParams:
    """Connection overrides plus the conversation search query and row limit."""
    params = extract_params(sources)
    openclaw = sources.get("openclaw", {})
    params["search"] = (
        openclaw.get("openclaw_search_query")
        or openclaw.get("search_query")
        or openclaw.get("search")
        or ""
    )
    params["limit"] = SEARCH_RESULT_LIMIT
    return params


def conversation_detail_params(sources: dict[str, dict]) -> OpenClawParams:
    """Connection overrides plus the conversation id, when the source carries one."""
    params = extract_params(sources)
    resolved_conversation_id = conversation_id(sources)
    if resolved_conversation_id:
        params["conversation_id"] = resolved_conversation_id
    return params
