"""OpenClaw MCP-backed bridge tools."""

from __future__ import annotations

from app.integrations.openclaw import (
    OpenClawConfig,
    OpenClawToolCallResult,
    build_openclaw_config,
    describe_openclaw_error,
    openclaw_config_from_env,
    openclaw_runtime_unavailable_reason,
)
from app.integrations.openclaw import (
    call_openclaw_tool as invoke_openclaw_mcp_tool,
)
from app.integrations.openclaw import (
    list_openclaw_tools as list_openclaw_mcp_tools,
)
from app.tools.tool_decorator import tool

OpenClawParams = dict[str, object]
OpenClawBridgeResponse = dict[str, object]
OpenClawConversationRow = dict[str, object]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _first_string(openclaw: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = str(openclaw.get(key, "")).strip()
        if value:
            return value
    return None


def _first_list(openclaw: dict[str, object], *keys: str) -> list[str]:
    for key in keys:
        values = _string_list(openclaw.get(key, []))
        if values:
            return values
    return []


def _openclaw_unavailable_response(
    error: str,
    *,
    tool_name: str | None = None,
    arguments: OpenClawParams | None = None,
) -> OpenClawBridgeResponse:
    payload: OpenClawBridgeResponse = {
        "source": "openclaw",
        "available": False,
        "error": error,
    }
    if tool_name:
        payload["tool"] = tool_name
    if arguments is not None:
        payload["arguments"] = arguments
    return payload


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


def _openclaw_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("openclaw", {}).get("connection_verified"))


def _openclaw_extract_params(sources: dict[str, dict]) -> OpenClawParams:
    openclaw = sources.get("openclaw", {})
    if not openclaw:
        return {}
    return {
        "openclaw_url": _first_string(openclaw, "openclaw_url", "url"),
        "openclaw_mode": _first_string(openclaw, "openclaw_mode", "mode"),
        "openclaw_token": _first_string(openclaw, "openclaw_token", "auth_token"),
        "openclaw_command": _first_string(openclaw, "openclaw_command", "command"),
        "openclaw_args": _first_list(openclaw, "openclaw_args", "args"),
    }


def _openclaw_conversation_id(sources: dict[str, dict]) -> str:
    openclaw = sources.get("openclaw", {})
    return str(
        openclaw.get("openclaw_conversation_id") or openclaw.get("conversation_id") or ""
    ).strip()


def _openclaw_conversation_params(sources: dict[str, dict]) -> OpenClawParams:
    params = _openclaw_extract_params(sources)
    openclaw = sources.get("openclaw", {})
    params["search"] = (
        openclaw.get("openclaw_search_query")
        or openclaw.get("search_query")
        or openclaw.get("search")
        or ""
    )
    params["limit"] = 10
    return params


def _openclaw_conversation_detail_params(sources: dict[str, dict]) -> OpenClawParams:
    params = _openclaw_extract_params(sources)
    conversation_id = _openclaw_conversation_id(sources)
    if conversation_id:
        params["conversation_id"] = conversation_id
    return params


def _normalize_tool_result(result: OpenClawToolCallResult) -> OpenClawBridgeResponse:
    if result.get("is_error"):
        return _openclaw_unavailable_response(
            str(result.get("text") or "OpenClaw MCP tool call failed."),
            tool_name=str(result.get("tool", "")).strip() or None,
            arguments=result.get("arguments", {}),
        )
    return {
        "source": "openclaw",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }


def _conversation_rows_from_result(result: OpenClawToolCallResult) -> list[OpenClawConversationRow]:
    structured = result.get("structured_content")
    if isinstance(structured, list):
        return [item for item in structured if isinstance(item, dict)]
    if isinstance(structured, dict):
        conversations = structured.get("conversations")
        if isinstance(conversations, list):
            return [item for item in conversations if isinstance(item, dict)]
        return [structured]
    return []


def _normalize_named_bridge_call(
    config: OpenClawConfig,
    *,
    tool_name: str,
    arguments: OpenClawParams,
) -> OpenClawBridgeResponse:
    try:
        result = invoke_openclaw_mcp_tool(config, tool_name, arguments)
    except Exception as err:
        return _openclaw_unavailable_response(
            describe_openclaw_error(err, config),
            tool_name=tool_name,
            arguments=arguments,
        )

    payload = _normalize_tool_result(result)
    if payload.get("available") is False:
        payload.setdefault("tool", tool_name)
        payload.setdefault("arguments", arguments)
    return payload


@tool(
    name="list_openclaw_tools",
    source="openclaw",
    description="List tools exposed by the configured OpenClaw MCP bridge.",
    use_cases=[
        "Inspecting which OpenClaw bridge tools are available before making a call",
        "Confirming whether conversation, event, or permissions tools are exposed",
    ],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array"},
        },
        "required": [],
    },
    is_available=_openclaw_available,
    extract_params=_openclaw_extract_params,
)
def list_openclaw_bridge_tools(
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: object,
) -> OpenClawBridgeResponse:
    """List tools available from the configured OpenClaw MCP bridge."""
    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        payload = _openclaw_unavailable_response("OpenClaw MCP integration is not configured.")
        payload["tools"] = []
        return payload

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        payload = _openclaw_unavailable_response(runtime_error)
        payload["tools"] = []
        return payload

    try:
        tools = list_openclaw_mcp_tools(config)
    except Exception as err:
        payload = _openclaw_unavailable_response(describe_openclaw_error(err, config))
        payload["tools"] = []
        return payload

    return {
        "source": "openclaw",
        "available": True,
        "transport": config.mode,
        "endpoint": config.command if config.mode == "stdio" else config.url,
        "tools": tools,
    }


@tool(
    name="search_openclaw_conversations",
    source="openclaw",
    description="Search recent OpenClaw conversations through the configured MCP bridge.",
    use_cases=[
        "Checking whether an engineer already discussed the failing service in OpenClaw",
        "Pulling recent OpenClaw context before querying external systems",
    ],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "search": {"type": "string"},
            "limit": {"type": "integer"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array"},
        },
        "required": [],
    },
    is_available=_openclaw_available,
    extract_params=_openclaw_conversation_params,
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
        payload = _openclaw_unavailable_response("OpenClaw MCP integration is not configured.")
        payload["conversations"] = []
        return payload

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        payload = _openclaw_unavailable_response(runtime_error)
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
        payload = _openclaw_unavailable_response(describe_openclaw_error(err, config))
        payload["conversations"] = []
        return payload

    payload = _normalize_tool_result(result)
    payload["search"] = search.strip()
    payload["conversations"] = _conversation_rows_from_result(result)
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
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array"},
        },
        "required": ["conversation_id"],
    },
    is_available=_openclaw_available,
    extract_params=_openclaw_conversation_detail_params,
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
        return _openclaw_unavailable_response("conversation_id is required.")

    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        return _openclaw_unavailable_response("OpenClaw MCP integration is not configured.")

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return _openclaw_unavailable_response(runtime_error)

    return _normalize_named_bridge_call(
        config,
        tool_name="conversations_get",
        arguments={"conversationId": normalized_conversation_id},
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
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "content": {"type": "string"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array"},
        },
        "required": ["conversation_id", "content"],
    },
    is_available=_openclaw_available,
    extract_params=_openclaw_conversation_detail_params,
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
        return _openclaw_unavailable_response("conversation_id is required.")
    if not normalized_content:
        return _openclaw_unavailable_response("content is required.")

    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        return _openclaw_unavailable_response("OpenClaw MCP integration is not configured.")

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return _openclaw_unavailable_response(runtime_error)

    return _normalize_named_bridge_call(
        config,
        tool_name="message_send",
        arguments={"conversationId": normalized_conversation_id, "content": normalized_content},
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
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array"},
        },
        "required": ["tool_name"],
    },
    is_available=_openclaw_available,
    extract_params=_openclaw_extract_params,
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
        return _openclaw_unavailable_response(
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
        return _openclaw_unavailable_response(
            "OpenClaw MCP integration is not configured.",
            tool_name=normalized_tool_name or None,
            arguments=arguments or {},
        )

    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return _openclaw_unavailable_response(
            runtime_error,
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    try:
        result = invoke_openclaw_mcp_tool(config, normalized_tool_name, arguments or {})
    except Exception as err:
        return _openclaw_unavailable_response(
            describe_openclaw_error(err, config),
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    return _normalize_tool_result(result)
