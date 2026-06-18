"""PostHog MCP-backed tools.

Exposes the hosted PostHog MCP server (product analytics, feature flags, error
tracking, experiments, HogQL queries, surveys, docs search, and more) to the
investigation and chat surfaces. The tool surface is intentionally generic — a
discovery tool plus a named-call tool — so it keeps working when PostHog adds
or renames individual MCP-side tools.
"""

from __future__ import annotations

import re

from app.integrations.posthog_mcp import (
    PostHogMCPConfig,
    PostHogMCPToolCallResult,
    build_posthog_mcp_config,
    describe_posthog_mcp_error,
    posthog_mcp_config_from_env,
    posthog_mcp_runtime_unavailable_reason,
)
from app.integrations.posthog_mcp import (
    call_posthog_mcp_tool as invoke_posthog_mcp_tool,
)
from app.integrations.posthog_mcp import (
    list_posthog_mcp_tools as list_posthog_mcp_server_tools,
)
from app.tools._telemetry import report_run_error
from app.tools.tool_decorator import tool

PostHogMCPParams = dict[str, object]
PostHogMCPResponse = dict[str, object]

_COMPONENT = "app.tools.PostHogMCPTool"

# The hosted PostHog MCP server exposes 240+ tools, each carrying a full JSON
# input schema. Returning every tool with its schema produces a payload that is
# multiple times larger than any model's context window (~580k estimated tokens
# observed against the live server), so the agent's context-budget enforcer
# trims the result away before the model ever sees a single tool name. The
# model then loops re-calling this tool and guesses tool names that don't exist.
# To stay well under the budget we return a compact, bounded listing by default
# (names + truncated descriptions, no schemas) and let the caller narrow with a
# filter or pull the full schema for a small, specific set of tools.
_MAX_DESCRIPTION_CHARS = 160
_MAX_TOOLS_RETURNED = 80
_MAX_SCHEMAS_RETURNED = 15


def _truncate_description(description: str) -> str:
    cleaned = " ".join(description.split())
    if len(cleaned) <= _MAX_DESCRIPTION_CHARS:
        return cleaned
    return cleaned[: _MAX_DESCRIPTION_CHARS - 1].rstrip() + "\u2026"


def _filter_tools(
    tools: list[dict[str, object]],
    name_filter: str,
) -> list[dict[str, object]]:
    """Keep tools whose name or description matches any whitespace/comma term."""
    terms = [term for term in re.split(r"[,\s]+", name_filter.lower()) if term]
    if not terms:
        return tools
    matched: list[dict[str, object]] = []
    for descriptor in tools:
        haystack = f"{descriptor.get('name', '')} {descriptor.get('description', '')}".lower()
        if any(term in haystack for term in terms):
            matched.append(descriptor)
    return matched


def _summarize_tool(
    descriptor: dict[str, object],
    *,
    include_schema: bool,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "name": str(descriptor.get("name", "")).strip(),
        "description": _truncate_description(str(descriptor.get("description", "") or "")),
    }
    schema = descriptor.get("input_schema")
    if include_schema and schema is not None:
        summary["input_schema"] = schema
    return summary


def _build_tool_listing(
    tools: list[dict[str, object]],
    *,
    name_filter: str | None,
    include_schema: bool,
) -> dict[str, object]:
    """Render a bounded, model-safe view of the discovered MCP tools."""
    total = len(tools)
    filtered = _filter_tools(tools, name_filter) if name_filter else list(tools)
    returned = filtered[:_MAX_TOOLS_RETURNED]
    # Only attach full schemas when the result set is small enough that doing so
    # keeps the payload bounded. Otherwise schemas would reintroduce the very
    # context blow-up this listing exists to prevent.
    attach_schema = include_schema and len(returned) <= _MAX_SCHEMAS_RETURNED

    summaries = [
        _summarize_tool(descriptor, include_schema=attach_schema) for descriptor in returned
    ]
    notes: list[str] = []
    if len(filtered) > len(returned):
        notes.append(
            f"Showing {len(returned)} of {len(filtered)} matching tools; "
            "pass name_filter to narrow the list."
        )
    elif total > len(returned):
        notes.append(
            f"Showing {len(returned)} of {total} tools; pass name_filter "
            "(e.g. 'events query sql') to narrow the list."
        )
    if include_schema and not attach_schema:
        notes.append(
            "input_schema omitted because too many tools matched; narrow to "
            f"{_MAX_SCHEMAS_RETURNED} or fewer tools with name_filter to include schemas."
        )
    if not include_schema:
        notes.append(
            "Schemas omitted to save context; call again with include_schema=true and a "
            "name_filter once you know which tool you need."
        )

    listing: dict[str, object] = {
        "total_tools": total,
        "matched_tools": len(filtered),
        "returned_tools": len(summaries),
        "tools": summaries,
    }
    if name_filter:
        listing["name_filter"] = name_filter
    if notes:
        listing["notes"] = " ".join(notes)
    return listing


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _first_string(source: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = str(source.get(key, "")).strip()
        if value:
            return value
    return None


def _first_list(source: dict[str, object], *keys: str) -> list[str]:
    for key in keys:
        values = _string_list(source.get(key, []))
        if values:
            return values
    return []


def _unavailable_response(
    error: str,
    *,
    tool_name: str | None = None,
    arguments: PostHogMCPParams | None = None,
) -> PostHogMCPResponse:
    payload: PostHogMCPResponse = {
        "source": "posthog_mcp",
        "available": False,
        "error": error,
    }
    if tool_name:
        payload["tool"] = tool_name
    if arguments is not None:
        payload["arguments"] = arguments
    return payload


_KNOWN_POSTHOG_MCP_MODES = frozenset({"stdio", "sse", "streamable-http"})


def _resolve_config(
    posthog_url: str | None,
    posthog_mode: str | None,
    posthog_token: str | None,
    posthog_command: str | None = None,
    posthog_args: list[str] | None = None,
) -> PostHogMCPConfig | None:
    env_config = posthog_mcp_config_from_env()
    if any((posthog_url, posthog_mode, posthog_token, posthog_command, posthog_args)):
        url = posthog_url or (env_config.url if env_config else "")
        command = posthog_command or (env_config.command if env_config else "")

        # The planner fills these connection params from a loose schema and often
        # guesses an invalid transport (e.g. "default") or asks for "stdio"
        # without a command. Drop anything we can't honor so we fall back to
        # inferring the transport from the configured command/url rather than
        # building a config that fails PostHogMCPConfig validation.
        requested_mode = (posthog_mode or "").strip().lower()
        if requested_mode not in _KNOWN_POSTHOG_MCP_MODES:
            requested_mode = ""
        if requested_mode == "stdio" and not command:
            requested_mode = ""

        inferred_mode = (
            requested_mode
            or ("stdio" if command else "")
            or ("streamable-http" if url else "")
            or (env_config.mode if env_config else "")
        )
        raw_config: PostHogMCPParams = {
            "url": url,
            "mode": inferred_mode,
            "auth_token": posthog_token or (env_config.auth_token if env_config else ""),
            "command": command,
            "args": posthog_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
            "organization_id": env_config.organization_id if env_config else "",
            "project_id": env_config.project_id if env_config else "",
            "features": list(env_config.features) if env_config else [],
            "read_only": env_config.read_only if env_config else True,
        }
        return build_posthog_mcp_config(raw_config)
    return env_config


def _posthog_mcp_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("posthog_mcp", {}).get("connection_verified"))


def _posthog_mcp_extract_params(sources: dict[str, dict]) -> PostHogMCPParams:
    posthog = sources.get("posthog_mcp", {})
    if not posthog:
        return {}
    return {
        "posthog_url": _first_string(posthog, "posthog_url", "url"),
        "posthog_mode": _first_string(posthog, "posthog_mode", "mode"),
        "posthog_token": _first_string(posthog, "posthog_token", "auth_token"),
        "posthog_command": _first_string(posthog, "posthog_command", "command"),
        "posthog_args": _first_list(posthog, "posthog_args", "args"),
    }


def _normalize_tool_result(result: PostHogMCPToolCallResult) -> PostHogMCPResponse:
    if result.get("is_error"):
        return _unavailable_response(
            str(result.get("text") or "PostHog MCP tool call failed."),
            tool_name=str(result.get("tool", "")).strip() or None,
            arguments=result.get("arguments", {}),
        )
    return {
        "source": "posthog_mcp",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }


@tool(
    name="list_posthog_tools",
    source="posthog_mcp",
    description=(
        "List the tools exposed by the configured PostHog MCP server. The server "
        "exposes 240+ tools, so this returns a compact, bounded listing (names + "
        "short descriptions, no schemas). Pass name_filter (e.g. 'events query sql') "
        "to narrow the list, and include_schema=true on a narrowed list to fetch the "
        "input schema of the specific tool you intend to call. To query events, call "
        "call_posthog_tool with tool_name='execute-sql' and a HogQL query."
    ),
    use_cases=[
        "Discovering which PostHog MCP tools are available before calling one",
        "Finding the right tool for a task by passing a name_filter (e.g. 'events query sql')",
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
                    "description contains any term are returned (e.g. 'events query sql')."
                ),
            },
            "include_schema": {
                "type": "boolean",
                "description": (
                    "Include each tool's full input_schema. Only honored when the "
                    "(filtered) result set is small; narrow with name_filter first."
                ),
            },
            "posthog_url": {"type": "string"},
            "posthog_mode": {"type": "string"},
            "posthog_token": {"type": "string"},
            "posthog_command": {"type": "string"},
            "posthog_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": [],
    },
    # Connection/transport settings are injected from the verified integration
    # config via extract_params and hidden from the model's tool schema. Exposing
    # them let the LLM supply hallucinated values (e.g. mode="mcp" or a base URL
    # without the /mcp path) that overrode the verified config and broke calls.
    injected_params=(
        "posthog_url",
        "posthog_mode",
        "posthog_token",
        "posthog_command",
        "posthog_args",
    ),
    is_available=_posthog_mcp_available,
    extract_params=_posthog_mcp_extract_params,
)
def list_posthog_tools(
    name_filter: str | None = None,
    include_schema: bool = False,
    posthog_url: str | None = None,
    posthog_mode: str | None = None,
    posthog_token: str | None = None,
    posthog_command: str | None = None,
    posthog_args: list[str] | None = None,
    **_kwargs: object,
) -> PostHogMCPResponse:
    """List tools available from the configured PostHog MCP server.

    Returns a compact, bounded view by default so the listing never overflows the
    agent's context budget (the live server's full schema dump is ~580k estimated
    tokens, multiples of any model's context window).
    """
    config = _resolve_config(
        posthog_url,
        posthog_mode,
        posthog_token,
        posthog_command,
        posthog_args,
    )
    if config is None:
        payload = _unavailable_response("PostHog MCP integration is not configured.")
        payload["tools"] = []
        return payload

    runtime_error = posthog_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        payload = _unavailable_response(runtime_error)
        payload["tools"] = []
        return payload

    try:
        tools = list_posthog_mcp_server_tools(config)
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_posthog_tools",
            source="posthog_mcp",
            component=_COMPONENT,
            method="list_posthog_mcp_server_tools",
            extras={"transport": config.mode},
        )
        payload = _unavailable_response(describe_posthog_mcp_error(err, config))
        payload["tools"] = []
        return payload

    listing = _build_tool_listing(
        [dict(descriptor) for descriptor in tools],
        name_filter=(name_filter or "").strip() or None,
        include_schema=bool(include_schema),
    )
    return {
        "source": "posthog_mcp",
        "available": True,
        "transport": config.mode,
        "endpoint": config.command if config.mode == "stdio" else config.url,
        **listing,
    }


@tool(
    name="call_posthog_tool",
    source="posthog_mcp",
    description=(
        "Call a named tool exposed by the configured PostHog MCP server "
        "(e.g. run a HogQL query, list feature flags, inspect an error)."
    ),
    use_cases=[
        "Running a HogQL/SQL query against the customer's PostHog project",
        "Listing or inspecting feature flags, experiments, or error-tracking issues",
        "Searching PostHog docs or fetching insight/dashboard data during an investigation",
    ],
    requires=["tool_name"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
            "posthog_url": {"type": "string"},
            "posthog_mode": {"type": "string"},
            "posthog_token": {"type": "string"},
            "posthog_command": {"type": "string"},
            "posthog_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["tool_name"],
    },
    # Only the MCP tool selection (tool_name) and its arguments are model-supplied.
    # Connection/transport settings are injected from the verified integration
    # config; see the note on list_posthog_tools for why they are hidden from the
    # model.
    injected_params=(
        "posthog_url",
        "posthog_mode",
        "posthog_token",
        "posthog_command",
        "posthog_args",
    ),
    is_available=_posthog_mcp_available,
    extract_params=_posthog_mcp_extract_params,
)
def call_posthog_tool(
    tool_name: str | None = None,
    arguments: PostHogMCPParams | None = None,
    posthog_url: str | None = None,
    posthog_mode: str | None = None,
    posthog_token: str | None = None,
    posthog_command: str | None = None,
    posthog_args: list[str] | None = None,
    **_kwargs: object,
) -> PostHogMCPResponse:
    """Call a specific PostHog MCP tool by name."""
    normalized_tool_name = (tool_name or "").strip()
    if not normalized_tool_name:
        return _unavailable_response(
            "tool_name is required to call a PostHog MCP tool.",
            arguments=arguments or {},
        )

    config = _resolve_config(
        posthog_url,
        posthog_mode,
        posthog_token,
        posthog_command,
        posthog_args,
    )
    if config is None:
        return _unavailable_response(
            "PostHog MCP integration is not configured.",
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    runtime_error = posthog_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return _unavailable_response(
            runtime_error,
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    try:
        result = invoke_posthog_mcp_tool(config, normalized_tool_name, arguments or {})
    except Exception as err:
        report_run_error(
            err,
            tool_name="call_posthog_tool",
            source="posthog_mcp",
            component=_COMPONENT,
            method="invoke_posthog_mcp_tool",
            extras={"mcp_tool": normalized_tool_name, "transport": config.mode},
        )
        return _unavailable_response(
            describe_posthog_mcp_error(err, config),
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    return _normalize_tool_result(result)
