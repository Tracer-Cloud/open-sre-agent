"""X (Twitter) MCP-backed tools.

Exposes the configured X MCP server (https://github.com/xdevplatform/xmcp) —
tweet creation, search, timelines, likes, retweets, bookmarks, and more — to
the investigation and chat surfaces. The tool surface is intentionally
generic — a discovery tool plus a named-call tool — so it keeps working when
X adds or renames individual MCP-side tools.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import CATALOG_ENTRIES_KEY, record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import (
    build_mcp_tool_listing,
    first_list,
    first_string,
    unavailable_response,
)
from integrations.mcp_transport import McpTransportMode
from integrations.x_mcp import (
    XMCPConfig,
    XMCPToolCallResult,
    build_x_mcp_config,
    describe_x_mcp_error,
    x_mcp_config_from_env,
    x_mcp_runtime_unavailable_reason,
)
from integrations.x_mcp import call_x_mcp_tool as invoke_x_mcp_tool
from integrations.x_mcp import list_x_mcp_tools as list_x_mcp_server_tools

XMCPParams = dict[str, object]
XMCPResponse = dict[str, object]

_COMPONENT = "integrations.x_mcp.tools.x_mcp_tool"


def _unavailable_response(
    error: str,
    *,
    tool_name: str | None = None,
    arguments: XMCPParams | None = None,
) -> XMCPResponse:
    return unavailable_response("x_mcp", error, tool_name=tool_name, arguments=arguments)


_KNOWN_X_MCP_MODES = frozenset(McpTransportMode)


def _has_evidence_payload(value: object) -> bool:
    """Return whether an MCP payload value contains reportable data."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, dict):
        # MCP TextContent envelopes carry a type marker even when their visible
        # text is empty. Only the text field determines whether that envelope
        # should create a report entry.
        if value.get("type") == "text" and "text" in value:
            return _has_evidence_payload(value["text"])

        # Structured boolean fields such as {"exists": false} are meaningful
        # tool results. Preserve both answers rather than treating false as an
        # absent payload while still ignoring a standalone boolean value.
        return any(_has_evidence_payload(item) for item in value.values()) or any(
            isinstance(item, bool) for item in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_has_evidence_payload(item) for item in value)
    return False


def _record_catalog_entry_once(
    evidence: dict[str, Any], *, source: str, label: str, summary: str
) -> None:
    """Record one catalog row for an accumulating canonical evidence key."""
    entries = evidence.get(CATALOG_ENTRIES_KEY)
    if isinstance(entries, list) and any(
        isinstance(entry, dict) and entry.get("source") == source for entry in entries
    ):
        return
    record_evidence_entry(evidence, source=source, label=label, summary=summary)


def _merge_tool_descriptor(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge repeated tool descriptors without dropping previously seen fields."""
    merged = dict(existing)
    for key, value in incoming.items():
        previous = merged.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            # MCP schemas can be nested, so merge them recursively to keep metadata
            # learned from either compact or schema-rich inventory responses.
            merged[key] = _merge_tool_descriptor(previous, value)
        else:
            merged[key] = value
    return merged


def _map_x_tool_listing(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    """Make a successful X MCP tool inventory citeable in incident reports."""
    tools = output.get("tools")
    if output.get("available") is not True or not isinstance(tools, list) or not tools:
        return

    inventory = evidence.setdefault("x_mcp_tools", [])
    if not isinstance(inventory, list):
        return

    # Merge repeated filtered listings by tool name without making descriptor richness
    # depend on whether compact or schema-rich inventory responses arrive first.
    positions = {
        str(item.get("name")): index
        for index, item in enumerate(inventory)
        if isinstance(item, dict) and item.get("name")
    }
    for descriptor in tools:
        if not isinstance(descriptor, dict) or not descriptor.get("name"):
            continue
        name = str(descriptor["name"])
        if name in positions:
            existing = inventory[positions[name]]
            if isinstance(existing, dict):
                inventory[positions[name]] = _merge_tool_descriptor(existing, descriptor)
        else:
            positions[name] = len(inventory)
            inventory.append(descriptor)

    if not inventory:
        evidence.pop("x_mcp_tools", None)
        return
    _record_catalog_entry_once(
        evidence,
        source="x_mcp_tools",
        label="X MCP Tool Inventory",
        summary="Available X MCP tools",
    )


def _map_x_tool_result(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    """Make a successful X MCP tool result citeable in incident reports."""
    payload_fields = ("text", "structured_content", "content")
    if output.get("available") is not True or not any(
        _has_evidence_payload(output.get(field)) for field in payload_fields
    ):
        return

    results = evidence.setdefault("x_mcp_results", [])
    if not isinstance(results, list):
        return
    # Keep only response data in the canonical report key; raw output remains available
    # through merge_tool_evidence without duplicating echoed call arguments here.
    result = {
        field: output.get(field)
        for field in ("tool", "text", "structured_content", "content")
        if field in output
    }
    results.append(result)

    _record_catalog_entry_once(
        evidence,
        source="x_mcp_results",
        label="X MCP Results",
        summary="X MCP tool results",
    )


def _resolve_config(
    x_url: str | None,
    x_mode: str | None,
    x_token: str | None,
    x_command: str | None = None,
    x_args: list[str] | None = None,
) -> XMCPConfig | None:
    env_config = x_mcp_config_from_env()
    if any((x_url, x_mode, x_token, x_command, x_args)):
        url = x_url or (env_config.url if env_config else "")
        command = x_command or (env_config.command if env_config else "")

        # The planner fills these connection params from a loose schema and often
        # guesses an invalid transport (e.g. "default") or asks for "stdio"
        # without a command. Drop anything we can't honor so we fall back to
        # inferring the transport from the configured command/url rather than
        # building a config that fails XMCPConfig validation.
        requested_mode = (x_mode or "").strip().lower()
        if requested_mode not in _KNOWN_X_MCP_MODES:
            requested_mode = ""
        if requested_mode == "stdio" and not command:
            requested_mode = ""

        inferred_mode = (
            requested_mode
            or ("stdio" if command else "")
            or ("streamable-http" if url else "")
            or (env_config.mode if env_config else "")
        )
        raw_config: XMCPParams = {
            "url": url,
            "mode": inferred_mode,
            "auth_token": x_token or (env_config.auth_token if env_config else ""),
            "bearer_token": env_config.bearer_token if env_config else "",
            "command": command,
            "args": x_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
        }
        return build_x_mcp_config(raw_config)
    return env_config


def _x_mcp_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("x_mcp", {}).get("connection_verified"))


def _x_mcp_extract_params(sources: dict[str, dict]) -> XMCPParams:
    x = sources.get("x_mcp", {})
    if not x:
        return {}
    return {
        "x_url": first_string(x, "x_url", "url"),
        "x_mode": first_string(x, "x_mode", "mode"),
        "x_token": first_string(x, "x_token", "auth_token"),
        "x_command": first_string(x, "x_command", "command"),
        "x_args": first_list(x, "x_args", "args"),
    }


def _normalize_tool_result(result: XMCPToolCallResult) -> XMCPResponse:
    if result.get("is_error"):
        return _unavailable_response(
            str(result.get("text") or "X MCP tool call failed."),
            tool_name=str(result.get("tool", "")).strip() or None,
            arguments=result.get("arguments", {}),
        )
    return {
        "source": "x_mcp",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }


@tool(
    name="list_x_tools",
    source="x_mcp",
    evidence_mapper=_map_x_tool_listing,
    description=(
        "List the tools exposed by the configured X (Twitter) MCP server. Pass "
        "name_filter (e.g. 'search tweet timeline') to narrow the list, and "
        "include_schema=true on a narrowed list to fetch the input schema of the "
        "specific tool you intend to call."
    ),
    use_cases=[
        "Discovering which X MCP tools are available before calling one",
        "Finding the right tool for a task by passing a name_filter (e.g. 'search tweet')",
        "Fetching the input schema of a specific tool with include_schema before calling it",
    ],
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    input_schema={
        "type": "object",
        "properties": {
            "name_filter": {
                "type": "string",
                "description": (
                    "Optional space- or comma-separated terms; tools whose name or "
                    "description contains any term are returned (e.g. 'search tweet')."
                ),
            },
            "include_schema": {
                "type": "boolean",
                "description": (
                    "Include each tool's full input_schema. Only honored when the "
                    "(filtered) result set is small; narrow with name_filter first."
                ),
            },
            "x_url": {"type": "string"},
            "x_mode": {"type": "string"},
            "x_token": {"type": "string"},
            "x_command": {"type": "string"},
            "x_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": [],
    },
    # Connection/transport settings are injected from the verified integration
    # config via extract_params and hidden from the model's tool schema. Exposing
    # them would let the LLM supply hallucinated values (e.g. mode="mcp" or a base
    # URL without the /mcp path) that override the verified config and break calls.
    injected_params=("x_url", "x_mode", "x_token", "x_command", "x_args"),
    is_available=_x_mcp_available,
    extract_params=_x_mcp_extract_params,
)
def list_x_tools(
    name_filter: str | None = None,
    include_schema: bool = False,
    x_url: str | None = None,
    x_mode: str | None = None,
    x_token: str | None = None,
    x_command: str | None = None,
    x_args: list[str] | None = None,
    **_kwargs: object,
) -> XMCPResponse:
    """List tools available from the configured X MCP server.

    Returns a compact, bounded view by default so the listing never overflows the
    agent's context budget.
    """
    config = _resolve_config(x_url, x_mode, x_token, x_command, x_args)
    if config is None:
        payload = _unavailable_response("X MCP integration is not configured.")
        payload["tools"] = []
        return payload

    runtime_error = x_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        payload = _unavailable_response(runtime_error)
        payload["tools"] = []
        return payload

    try:
        tools = list_x_mcp_server_tools(config)
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_x_tools",
            source="x_mcp",
            component=_COMPONENT,
            method="list_x_mcp_server_tools",
            extras={"transport": config.mode},
        )
        payload = _unavailable_response(describe_x_mcp_error(err, config))
        payload["tools"] = []
        return payload

    listing = build_mcp_tool_listing(
        [dict(descriptor) for descriptor in tools],
        name_filter=(name_filter or "").strip() or None,
        include_schema=bool(include_schema),
    )
    return {
        "source": "x_mcp",
        "available": True,
        "transport": config.mode,
        "endpoint": config.command if config.mode == "stdio" else config.url,
        **listing,
    }


@tool(
    name="call_x_tool",
    source="x_mcp",
    evidence_mapper=_map_x_tool_result,
    description=(
        "Call a named tool exposed by the configured X (Twitter) MCP server "
        "(e.g. search tweets, inspect a user's timeline, look up a tweet by ID)."
    ),
    use_cases=[
        "Searching X/Twitter for posts related to an incident (e.g. customer reports, outage chatter)",
        "Inspecting a user's timeline or a specific tweet during an investigation",
    ],
    requires=["tool_name"],
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
            "x_url": {"type": "string"},
            "x_mode": {"type": "string"},
            "x_token": {"type": "string"},
            "x_command": {"type": "string"},
            "x_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["tool_name"],
    },
    # Only the MCP tool selection (tool_name) and its arguments are model-supplied.
    # Connection/transport settings are injected from the verified integration
    # config; see the note on list_x_tools for why they are hidden from the model.
    injected_params=("x_url", "x_mode", "x_token", "x_command", "x_args"),
    is_available=_x_mcp_available,
    extract_params=_x_mcp_extract_params,
)
def call_x_tool(
    tool_name: str | None = None,
    arguments: XMCPParams | None = None,
    x_url: str | None = None,
    x_mode: str | None = None,
    x_token: str | None = None,
    x_command: str | None = None,
    x_args: list[str] | None = None,
    **_kwargs: object,
) -> XMCPResponse:
    """Call a specific X MCP tool by name."""
    normalized_tool_name = (tool_name or "").strip()
    if not normalized_tool_name:
        return _unavailable_response(
            "tool_name is required to call an X MCP tool.",
            arguments=arguments or {},
        )

    config = _resolve_config(x_url, x_mode, x_token, x_command, x_args)
    if config is None:
        return _unavailable_response(
            "X MCP integration is not configured.",
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    runtime_error = x_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return _unavailable_response(
            runtime_error,
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    try:
        result = invoke_x_mcp_tool(config, normalized_tool_name, arguments or {})
    except Exception as err:
        report_run_error(
            err,
            tool_name="call_x_tool",
            source="x_mcp",
            component=_COMPONENT,
            method="invoke_x_mcp_tool",
            extras={"mcp_tool": normalized_tool_name, "transport": config.mode},
        )
        return _unavailable_response(
            describe_x_mcp_error(err, config),
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    return _normalize_tool_result(result)
