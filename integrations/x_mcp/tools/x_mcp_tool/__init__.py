"""X (Twitter) MCP-backed tools.

Exposes the configured X MCP server (https://github.com/xdevplatform/xmcp) —
tweet creation, search, timelines, likes, retweets, bookmarks, and more — to
the investigation and chat surfaces. The tool surface is intentionally
generic — a discovery tool plus a named-call tool — so it keeps working when
X adds or renames individual MCP-side tools.
"""

from __future__ import annotations

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.mcp_params import first_list, first_string
from core.tool_framework.utils.mcp_tool_listing import build_mcp_tool_listing
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
    payload: XMCPResponse = {
        "source": "x_mcp",
        "available": False,
        "error": error,
    }
    if tool_name:
        payload["tool"] = tool_name
    if arguments is not None:
        payload["arguments"] = arguments
    return payload


_KNOWN_X_MCP_MODES = frozenset({"stdio", "sse", "streamable-http"})


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
    surfaces=("investigation", "chat"),
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
    description=(
        "Call a named tool exposed by the configured X (Twitter) MCP server "
        "(e.g. search tweets, inspect a user's timeline, look up a tweet by ID)."
    ),
    use_cases=[
        "Searching X/Twitter for posts related to an incident (e.g. customer reports, outage chatter)",
        "Inspecting a user's timeline or a specific tweet during an investigation",
    ],
    requires=["tool_name"],
    surfaces=("investigation", "chat"),
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
