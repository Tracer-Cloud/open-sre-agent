"""OpenClaw MCP-backed bridge tools.

Package layout (separation of concerns):

- ``models.py``   — payload type aliases shared by the whole package.
- ``results.py``  — pure shaping of MCP results/failures into agent payloads.
- ``params.py``   — availability + ``extract_params`` reads over the agent-state
  ``openclaw`` source.
- ``evidence.py`` — report catalog mappers for the read tools.
- ``__init__.py`` — this file: config resolution, MCP dispatch, and the
  ``@tool`` entrypoints. They stay here because the tool registry discovers a
  package's tools on its own module (``TOOL_MODULES`` would be needed to look
  into sub-modules) and tests patch the bridge callables on this module.
"""

from __future__ import annotations

from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import build_mcp_tool_listing
from integrations.openclaw import (
    OpenClawConfig,
    build_openclaw_config,
    describe_openclaw_error,
    openclaw_config_from_env,
    openclaw_runtime_unavailable_reason,
)
from integrations.openclaw import (
    call_openclaw_tool as invoke_openclaw_mcp_tool,
)
from integrations.openclaw import (
    list_openclaw_tools as list_openclaw_mcp_tools,
)
from integrations.openclaw.tools.openclaw_mcp_tool.evidence import (
    map_call_openclaw_tool,
    map_get_openclaw_conversation,
    map_list_openclaw_tools,
    map_search_openclaw_conversations,
)
from integrations.openclaw.tools.openclaw_mcp_tool.models import (
    OpenClawBridgeResponse,
    OpenClawParams,
)
from integrations.openclaw.tools.openclaw_mcp_tool.params import (
    conversation_detail_params,
    conversation_search_params,
    extract_params,
    is_available,
)
from integrations.openclaw.tools.openclaw_mcp_tool.results import (
    conversation_rows_from_result,
    normalize_tool_result,
    unavailable_result,
)


def _resolve_config(
    openclaw_url: str | None,
    openclaw_mode: str | None,
    openclaw_token: str | None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
) -> OpenClawConfig | None:
    env_config = openclaw_config_from_env()
    if any((openclaw_url, openclaw_mode, openclaw_token, openclaw_command, openclaw_args)):
        inferred_mode = (
            openclaw_mode
            or ("stdio" if openclaw_command else "")
            or ("streamable-http" if openclaw_url else "")
            or (env_config.mode if env_config else "")
        )
        raw_config: OpenClawParams = {
            "url": openclaw_url or (env_config.url if env_config else ""),
            "mode": inferred_mode,
            "auth_token": openclaw_token or (env_config.auth_token if env_config else ""),
            "command": openclaw_command or (env_config.command if env_config else ""),
            "args": openclaw_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
        }
        return build_openclaw_config(raw_config)
    return env_config


def _normalize_named_bridge_call(
    config: OpenClawConfig,
    *,
    tool_name: str,
    arguments: OpenClawParams,
    surface_tool_name: str,
) -> OpenClawBridgeResponse:
    """Invoke a named MCP tool and normalise its result.

    ``tool_name`` is the MCP-side tool identifier (e.g. ``conversations_get``);
    ``surface_tool_name`` is the OpenSRE registered tool name that this call
    is running on behalf of (e.g. ``get_openclaw_conversation``) so the Sentry
    ``tool_name`` tag matches the tool's declared metadata.
    """
    try:
        result = invoke_openclaw_mcp_tool(config, tool_name, arguments)
    except Exception as err:
        report_run_error(
            err,
            tool_name=surface_tool_name,
            source="openclaw",
            component="integrations.openclaw.tools.openclaw_mcp_tool",
            method=f"invoke_openclaw_mcp_tool('{tool_name}')",
            extras={"mcp_tool": tool_name, "transport": config.mode},
        )
        return unavailable_result(
            describe_openclaw_error(err, config),
            tool_name=tool_name,
            arguments=arguments,
        )

    payload = normalize_tool_result(result)
    if payload.get("available") is False:
        payload.setdefault("tool", tool_name)
        payload.setdefault("arguments", arguments)
    return payload


@tool(
    name="list_openclaw_tools",
    source="openclaw",
    description=(
        "List tools exposed by the configured OpenClaw MCP bridge. Returns a "
        "compact, bounded listing (names + short descriptions, no schemas) so it "
        "never overflows the agent's context budget. Pass name_filter (e.g. "
        "'conversation event permission') to narrow the list, and include_schema=true "
        "on a narrowed list to fetch the input schema of the tool you intend to call."
    ),
    use_cases=[
        "Inspecting which OpenClaw bridge tools are available before making a call",
        "Finding the right tool by passing a name_filter (e.g. 'conversation event permission')",
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
                    "description contains any term are returned (e.g. 'conversation event')."
                ),
            },
            "include_schema": {
                "type": "boolean",
                "description": (
                    "Include each tool's full input_schema. Only honored when the "
                    "(filtered) result set is small; narrow with name_filter first."
                ),
            },
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": [],
    },
    is_available=is_available,
    extract_params=extract_params,
    evidence_mapper=map_list_openclaw_tools,
)
def list_openclaw_bridge_tools(
    name_filter: str | None = None,
    include_schema: bool = False,
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: object,
) -> OpenClawBridgeResponse:
    """List tools available from the configured OpenClaw MCP bridge.

    Returns a compact, bounded view by default so the listing never overflows the
    agent's context budget.
    """
    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        payload = unavailable_result("OpenClaw MCP integration is not configured.")
        payload["tools"] = []
        return payload

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        payload = unavailable_result(runtime_error)
        payload["tools"] = []
        return payload

    try:
        tools = list_openclaw_mcp_tools(config)
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_openclaw_tools",
            source="openclaw",
            component="integrations.openclaw.tools.openclaw_mcp_tool",
            method="list_openclaw_mcp_tools",
            extras={"transport": config.mode},
        )
        payload = unavailable_result(describe_openclaw_error(err, config))
        payload["tools"] = []
        return payload

    listing = build_mcp_tool_listing(
        [dict(descriptor) for descriptor in tools],
        name_filter=(name_filter or "").strip() or None,
        include_schema=bool(include_schema),
        filter_example="conversation event permission",
    )
    return {
        "source": "openclaw",
        "available": True,
        "transport": config.mode,
        "endpoint": config.command if config.mode == "stdio" else config.url,
        **listing,
    }


@tool(
    name="search_openclaw_conversations",
    source="openclaw",
    description="Search recent OpenClaw conversations through the configured MCP bridge.",
    use_cases=[
        "Checking whether an engineer already discussed the failing service in OpenClaw",
        "Pulling recent OpenClaw context before querying external systems",
    ],
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    input_schema={
        "type": "object",
        "properties": {
            "search": {"type": "string"},
            "limit": {"type": "integer"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": [],
    },
    is_available=is_available,
    extract_params=conversation_search_params,
    evidence_mapper=map_search_openclaw_conversations,
)
def search_openclaw_conversations(
    search: str = "",
    limit: int = 10,
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: object,
) -> OpenClawBridgeResponse:
    """Search recent OpenClaw conversations through the MCP bridge."""
    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        payload = unavailable_result("OpenClaw MCP integration is not configured.")
        payload["conversations"] = []
        return payload

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        payload = unavailable_result(runtime_error)
        payload["conversations"] = []
        return payload

    arguments: OpenClawParams = {
        "limit": max(1, min(limit, 25)),
        "includeDerivedTitles": True,
        "includeLastMessage": True,
    }
    if search.strip():
        arguments["search"] = search.strip()

    try:
        result = invoke_openclaw_mcp_tool(config, "conversations_list", arguments)
    except Exception as err:
        report_run_error(
            err,
            tool_name="search_openclaw_conversations",
            source="openclaw",
            component="integrations.openclaw.tools.openclaw_mcp_tool",
            method="invoke_openclaw_mcp_tool('conversations_list')",
            extras={"transport": config.mode},
        )
        payload = unavailable_result(describe_openclaw_error(err, config))
        payload["conversations"] = []
        return payload

    payload = normalize_tool_result(result)
    payload["search"] = search.strip()
    payload["conversations"] = conversation_rows_from_result(result)
    return payload


@tool(
    name="get_openclaw_conversation",
    source="openclaw",
    description="Fetch one OpenClaw conversation by id through the configured MCP bridge.",
    use_cases=[
        "Reading the full context of an OpenClaw conversation that may explain the active alert",
        "Pulling the latest assistant and engineer messages before continuing an investigation",
    ],
    requires=["conversation_id"],
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    input_schema={
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["conversation_id"],
    },
    is_available=is_available,
    extract_params=conversation_detail_params,
    evidence_mapper=map_get_openclaw_conversation,
)
def get_openclaw_conversation(
    conversation_id: str | None = None,
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: object,
) -> OpenClawBridgeResponse:
    """Fetch a specific OpenClaw conversation."""
    normalized_conversation_id = (conversation_id or "").strip()
    if not normalized_conversation_id:
        return unavailable_result("conversation_id is required.")

    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        return unavailable_result("OpenClaw MCP integration is not configured.")

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return unavailable_result(runtime_error)

    return _normalize_named_bridge_call(
        config,
        tool_name="conversations_get",
        arguments={"conversationId": normalized_conversation_id},
        surface_tool_name="get_openclaw_conversation",
    )


@tool(
    name="send_openclaw_message",
    source="openclaw",
    description="Send a message into an existing OpenClaw conversation.",
    use_cases=[
        "Writing investigation findings back into a conversation an engineer is already using",
        "Appending a short remediation note or next-step summary to an OpenClaw thread",
    ],
    requires=["conversation_id"],
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    input_schema={
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "content": {"type": "string"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["conversation_id", "content"],
    },
    is_available=is_available,
    extract_params=conversation_detail_params,
)
def send_openclaw_message(
    conversation_id: str | None = None,
    content: str | None = None,
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: object,
) -> OpenClawBridgeResponse:
    """Send a message into an OpenClaw conversation."""
    normalized_conversation_id = (conversation_id or "").strip()
    normalized_content = (content or "").strip()
    if not normalized_conversation_id:
        return unavailable_result("conversation_id is required.")
    if not normalized_content:
        return unavailable_result("content is required.")

    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        return unavailable_result("OpenClaw MCP integration is not configured.")

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return unavailable_result(runtime_error)

    return _normalize_named_bridge_call(
        config,
        tool_name="message_send",
        arguments={"conversationId": normalized_conversation_id, "content": normalized_content},
        surface_tool_name="send_openclaw_message",
    )


@tool(
    name="call_openclaw_tool",
    source="openclaw",
    description="Call a named tool exposed by the configured OpenClaw MCP bridge.",
    use_cases=[
        "Reading OpenClaw conversations and recent transcript history",
        "Polling OpenClaw event queues or responding through an existing route",
    ],
    requires=["tool_name"],
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["tool_name"],
    },
    is_available=is_available,
    extract_params=extract_params,
    evidence_mapper=map_call_openclaw_tool,
)
def call_openclaw_bridge_tool(
    tool_name: str | None = None,
    arguments: OpenClawParams | None = None,
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: object,
) -> OpenClawBridgeResponse:
    """Call a specific OpenClaw MCP bridge tool."""
    normalized_tool_name = (tool_name or "").strip()
    if not normalized_tool_name:
        return unavailable_result(
            "tool_name is required to call an OpenClaw MCP tool.",
            arguments=arguments or {},
        )

    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        return unavailable_result(
            "OpenClaw MCP integration is not configured.",
            tool_name=normalized_tool_name or None,
            arguments=arguments or {},
        )

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return unavailable_result(
            runtime_error,
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    try:
        result = invoke_openclaw_mcp_tool(config, normalized_tool_name, arguments or {})
    except Exception as err:
        report_run_error(
            err,
            tool_name="call_openclaw_tool",
            source="openclaw",
            component="integrations.openclaw.tools.openclaw_mcp_tool",
            method="invoke_openclaw_mcp_tool",
            extras={"mcp_tool": normalized_tool_name, "transport": config.mode},
        )
        return unavailable_result(
            describe_openclaw_error(err, config),
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    return normalize_tool_result(result)
