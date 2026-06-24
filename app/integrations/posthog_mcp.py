"""Shared PostHog MCP integration helpers.

PostHog ships a hosted Model Context Protocol (MCP) server that exposes its
products — product analytics, feature flags, error tracking, experiments,
HogQL queries, surveys, and more — as function-calling tools. This module
centralizes PostHog MCP configuration, validation, and tool-calling so the
onboarding wizard, verify CLI, chat tools, and investigation actions all share
the same transport and parsing logic.

This is distinct from ``app/integrations/posthog.py``, which is a narrow REST
client used for bounce-rate alerting. The MCP integration is the general,
customer-connected tool surface.

Supported transports:
  - streamable-http  (default) — HTTP-based MCP via Streamable HTTP (hosted)
  - sse              — Server-Sent Events MCP transport
  - stdio            — subprocess-based MCP (e.g. ``npx -y @posthog/mcp-server``)

Authentication uses a PostHog personal API key sent as a bearer token. See
https://posthog.com/docs/model-context-protocol for the hosted endpoint and
the ``MCP Server`` personal-API-key preset.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Coroutine, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from mcp import ClientSession, StdioServerParameters, types  # type: ignore[import-not-found]
from mcp.client.sse import sse_client  # type: ignore[import-not-found]
from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]
from pydantic import Field, field_validator, model_validator
from typing_extensions import TypedDict

from app.integrations._validation_helpers import report_classify_failure, report_validation_failure
from app.integrations.mcp_streamable_http_compat import streamable_http_client
from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

DEFAULT_POSTHOG_MCP_URL = "https://mcp.posthog.com/mcp"
DEFAULT_POSTHOG_MCP_MODE: Literal["streamable-http", "sse", "stdio"] = "streamable-http"

# PostHog routes EU accounts automatically, but the dedicated EU host is exposed
# for users who prefer to pin it explicitly.
POSTHOG_MCP_EU_URL = "https://mcp-eu.posthog.com/mcp"


class PostHogMCPToolDescriptor(TypedDict):
    """A tool exposed by the PostHog MCP server."""

    name: str
    description: str
    input_schema: object | None


class PostHogMCPContentItem(TypedDict, total=False):
    """Normalized content item returned by an MCP tool call."""

    type: str
    text: str
    uri: str
    mime_type: str


class PostHogMCPToolCallResult(TypedDict, total=False):
    """Normalized response from a PostHog MCP tool call."""

    is_error: bool
    text: str
    content: list[PostHogMCPContentItem]
    structured_content: object | None
    tool: str
    arguments: dict[str, object]


class PostHogMCPConfig(StrictConfigModel):
    """Normalized PostHog MCP connection settings."""

    url: str = DEFAULT_POSTHOG_MCP_URL
    mode: Literal["stdio", "sse", "streamable-http"] = DEFAULT_POSTHOG_MCP_MODE
    auth_token: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    headers: dict[str, str] = Field(default_factory=dict)
    organization_id: str = ""
    project_id: str = ""
    features: tuple[str, ...] = ()
    read_only: bool = True
    timeout_seconds: float = Field(default=20.0, gt=0)
    integration_id: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> str:
        normalized = str(value or "").strip()
        return normalized.rstrip("/") if normalized else ""

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> str:
        normalized = str(value or DEFAULT_POSTHOG_MCP_MODE).strip().lower()
        normalized = normalized or DEFAULT_POSTHOG_MCP_MODE
        # Generic aliases that callers (env, store, or the planner) may emit
        # all map to the default hosted HTTP transport rather than tripping the
        # Literal validation. "default" is what the planner tends to guess when
        # it has no explicit transport to pass.
        if normalized in {"mcp", "default", "http", "https", "streamable_http"}:
            return DEFAULT_POSTHOG_MCP_MODE
        return normalized

    @field_validator("auth_token", mode="before")
    @classmethod
    def _normalize_auth_token(cls, value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        return token

    @field_validator("command", mode="before")
    @classmethod
    def _normalize_command(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("organization_id", "project_id", mode="before")
    @classmethod
    def _normalize_identifier(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("args", mode="before")
    @classmethod
    def _normalize_args(cls, value: object) -> tuple[str, ...]:
        if value is None or not isinstance(value, (list, tuple, set)):
            return ()
        return tuple(str(arg).strip() for arg in value if str(arg).strip())

    @field_validator("features", mode="before")
    @classmethod
    def _normalize_features(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            candidates = value.replace(",", " ").split()
        elif isinstance(value, (list, tuple, set)):
            candidates = [str(item) for item in value]
        else:
            return ()
        return tuple(item.strip().lower() for item in candidates if item.strip())

    @field_validator("headers", mode="before")
    @classmethod
    def _normalize_headers(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}

    @model_validator(mode="after")
    def _validate_transport_requirements(self) -> PostHogMCPConfig:
        if self.mode == "stdio" and not self.command:
            raise ValueError("PostHog MCP mode 'stdio' requires a non-empty command.")
        if self.mode != "stdio" and not self.url:
            raise ValueError(f"PostHog MCP mode '{self.mode}' requires a non-empty url.")
        return self

    @property
    def is_configured(self) -> bool:
        if self.mode == "stdio":
            return bool(self.command)
        return bool(self.url)

    @property
    def session_url(self) -> str:
        """URL with the ``features`` query parameter merged in (HTTP/SSE only)."""
        if self.mode == "stdio" or not self.url:
            return self.url
        if not self.features:
            return self.url
        parsed = urlparse(self.url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if "features" not in query:
            query["features"] = ",".join(self.features)
        new_query = urlencode(query)
        return urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
        )

    @property
    def request_headers(self) -> dict[str, str]:
        headers = {k: v for k, v in self.headers.items() if v}
        if self.auth_token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if self.organization_id and "x-posthog-organization-id" not in headers:
            headers["x-posthog-organization-id"] = self.organization_id
        if self.project_id and "x-posthog-project-id" not in headers:
            headers["x-posthog-project-id"] = self.project_id
        if self.read_only and "x-posthog-read-only" not in headers:
            headers["x-posthog-read-only"] = "true"
        return headers


@dataclass(frozen=True)
class PostHogMCPValidationResult:
    """Result of validating a PostHog MCP connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def build_posthog_mcp_config(raw: Mapping[str, object] | None) -> PostHogMCPConfig:
    """Build a normalized PostHog MCP config object from env/store data."""
    payload = dict(raw or {})
    allowed = set(PostHogMCPConfig.model_fields)
    sanitized = {key: value for key, value in payload.items() if key in allowed}
    return PostHogMCPConfig.model_validate(sanitized)


def posthog_mcp_config_from_env() -> PostHogMCPConfig | None:
    """Load a PostHog MCP config from environment variables."""
    mode = os.getenv("POSTHOG_MCP_MODE", DEFAULT_POSTHOG_MCP_MODE).strip().lower()
    url = os.getenv("POSTHOG_MCP_URL", "").strip()
    command = os.getenv("POSTHOG_MCP_COMMAND", "").strip()
    auth_token = os.getenv("POSTHOG_MCP_AUTH_TOKEN", "").strip()
    args_env = os.getenv("POSTHOG_MCP_ARGS", "").strip()
    read_only_env = os.getenv("POSTHOG_MCP_READ_ONLY", "").strip().lower()

    mode = mode or DEFAULT_POSTHOG_MCP_MODE
    if mode == "stdio":
        if not command:
            return None
    else:
        # Hosted PostHog MCP requires an API key; without one there is nothing to do.
        if not auth_token:
            return None
        if not url:
            url = DEFAULT_POSTHOG_MCP_URL

    read_only = read_only_env not in ("false", "0", "no") if read_only_env else True

    return build_posthog_mcp_config(
        {
            "url": url,
            "mode": mode,
            "command": command,
            "args": [part for part in args_env.split() if part],
            "auth_token": auth_token,
            "organization_id": os.getenv("POSTHOG_MCP_ORGANIZATION_ID", "").strip(),
            "project_id": os.getenv("POSTHOG_MCP_PROJECT_ID", "").strip(),
            "features": os.getenv("POSTHOG_MCP_FEATURES", "").strip(),
            "read_only": read_only,
        }
    )


def posthog_mcp_runtime_unavailable_reason(config: PostHogMCPConfig) -> str | None:
    """Return a setup error when the config cannot be used."""
    if not config.is_configured:
        return "PostHog MCP is not configured: provide a URL (HTTP/SSE) or command (stdio)."
    if config.mode != "stdio" and not config.auth_token:
        return (
            "PostHog MCP requires a personal API key. Create one with the `MCP Server` preset "
            "and set POSTHOG_MCP_AUTH_TOKEN."
        )
    return None


@asynccontextmanager
async def _open_posthog_mcp_session(config: PostHogMCPConfig) -> AsyncIterator[ClientSession]:
    """Open an MCP client session for PostHog using the configured transport."""
    stack = AsyncExitStack()
    try:
        if config.mode == "stdio":
            if not config.command:
                raise ValueError(
                    "Invalid PostHog MCP config: mode=stdio requires command "
                    "(set POSTHOG_MCP_COMMAND or pass command in config)."
                )
            server_params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env={
                    **os.environ,
                    # Suppress terminal control codes so the MCP server's stdout
                    # stays clean JSON-RPC (mirrors github_mcp.py mitigation).
                    "NO_COLOR": "1",
                    "TERM": "dumb",
                    **(
                        {"POSTHOG_AUTH_HEADER": f"Bearer {config.auth_token}"}
                        if config.auth_token
                        else {}
                    ),
                    **(
                        {"POSTHOG_PERSONAL_API_KEY": config.auth_token} if config.auth_token else {}
                    ),
                },
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))

        elif config.mode == "sse":
            if not config.url:
                raise ValueError(
                    "Invalid PostHog MCP config: mode=sse requires url "
                    "(set POSTHOG_MCP_URL, e.g. https://mcp.posthog.com/sse)."
                )
            read_stream, write_stream = await stack.enter_async_context(
                sse_client(
                    config.session_url,
                    headers=config.request_headers,
                    timeout=config.timeout_seconds,
                    sse_read_timeout=max(60.0, config.timeout_seconds),
                )
            )

        elif config.mode == "streamable-http":
            if not config.url:
                raise ValueError(
                    "Invalid PostHog MCP config: mode=streamable-http requires url "
                    "(set POSTHOG_MCP_URL)."
                )
            read_timeout = max(60.0, config.timeout_seconds)
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=config.request_headers,
                    timeout=httpx.Timeout(config.timeout_seconds, read=read_timeout),
                )
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    config.session_url,
                    http_client=http_client,
                    headers=config.request_headers,
                    timeout=config.timeout_seconds,
                    sse_read_timeout=read_timeout,
                )
            )

        else:
            raise ValueError(
                f"Unsupported PostHog MCP mode '{config.mode}'. "
                "Supported modes: stdio, sse, streamable-http."
            )

        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        yield session

    finally:
        await stack.aclose()


def _run_async(coro: Coroutine[object, object, object]) -> object:
    try:
        return asyncio.run(coro)
    except BaseException:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise


def _root_cause_message(exc: BaseException) -> str:
    """Best-effort unwrap for ExceptionGroup/TaskGroup chains."""
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _root_cause_message(exc.exceptions[0])
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, BaseException):
        return _root_cause_message(cause)
    context = getattr(exc, "__context__", None)
    if isinstance(context, BaseException):
        return _root_cause_message(context)
    if isinstance(exc, TimeoutError):
        return "PostHog MCP tool call timed out"
    return str(exc).strip() or exc.__class__.__name__


def describe_posthog_mcp_error(err: BaseException, config: PostHogMCPConfig) -> str:
    """Render a human-readable error with a setup hint when useful."""
    detail = _root_cause_message(err)
    hints: list[str] = []

    if isinstance(err, httpx.HTTPStatusError) and err.response.status_code in (401, 403):
        hints.append(
            "Authentication failed. Check POSTHOG_MCP_AUTH_TOKEN is a valid personal API key "
            "created with the `MCP Server` preset."
        )
    elif config.mode != "stdio" and not config.auth_token:
        hints.append("No API key configured. Set POSTHOG_MCP_AUTH_TOKEN to a personal API key.")

    if "timed out" in detail.lower():
        hints.append(
            f"The tool did not return within {config.timeout_seconds:.1f}s. "
            "Raise PostHogMCPConfig.timeout_seconds if the tool is expected to be slow."
        )

    if hints:
        return f"{detail} Hint: {' '.join(hints)}"
    return detail


def _tool_result_to_dict(result: types.CallToolResult) -> PostHogMCPToolCallResult:
    text_parts: list[str] = []
    content_items: list[PostHogMCPContentItem] = []

    for item in result.content:
        if isinstance(item, types.TextContent):
            text_parts.append(item.text)
            content_items.append({"type": "text", "text": item.text})
        elif isinstance(item, types.EmbeddedResource):
            resource = item.resource
            if isinstance(resource, types.TextResourceContents):
                content_items.append(
                    {
                        "type": "resource_text",
                        "uri": str(resource.uri),
                        "text": resource.text,
                    }
                )
                text_parts.append(resource.text)
            elif isinstance(resource, types.BlobResourceContents):
                content_items.append(
                    {
                        "type": "resource_blob",
                        "uri": str(resource.uri),
                        "mime_type": resource.mimeType or "",
                    }
                )
        else:
            content_items.append({"type": getattr(item, "type", "unknown")})

    structured = getattr(result, "structuredContent", None)
    text_output = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
    return {
        "is_error": bool(result.isError),
        "text": text_output,
        "content": content_items,
        "structured_content": structured,
    }


async def _list_tools_async(config: PostHogMCPConfig) -> list[types.Tool]:
    async with _open_posthog_mcp_session(config) as session:
        result = await session.list_tools()
        return list(result.tools)


def _list_tools_sync(config: PostHogMCPConfig) -> list[types.Tool]:
    return cast(list[types.Tool], _run_async(_list_tools_async(config)))


def list_posthog_mcp_tools(config: PostHogMCPConfig) -> list[PostHogMCPToolDescriptor]:
    """List available tools from the PostHog MCP server."""
    tools = _list_tools_sync(config)
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": getattr(tool, "inputSchema", None),
        }
        for tool in tools
    ]


async def _call_tool_async(
    config: PostHogMCPConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> PostHogMCPToolCallResult:
    async with _open_posthog_mcp_session(config) as session:
        # Bound the call uniformly across transports so a hung MCP tool cannot
        # block the investigation pipeline indefinitely.
        result = await asyncio.wait_for(
            session.call_tool(tool_name, arguments or {}),
            timeout=config.timeout_seconds,
        )
        payload = _tool_result_to_dict(result)
        payload["tool"] = tool_name
        payload["arguments"] = arguments or {}
        return payload


def call_posthog_mcp_tool(
    config: PostHogMCPConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> PostHogMCPToolCallResult:
    """Call a PostHog MCP tool and normalize the result."""
    return cast(
        PostHogMCPToolCallResult,
        _run_async(_call_tool_async(config, tool_name, arguments)),
    )


def validate_posthog_mcp_config(config: PostHogMCPConfig) -> PostHogMCPValidationResult:
    """Validate PostHog MCP connectivity by listing available tools."""
    runtime_error = posthog_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return PostHogMCPValidationResult(
            ok=False,
            detail=f"PostHog MCP validation failed: {runtime_error}",
        )

    try:
        tools = list_posthog_mcp_tools(config)
        tool_names = tuple(sorted(t["name"] for t in tools))
        endpoint = config.command if config.mode == "stdio" else config.url
        if not tool_names:
            return PostHogMCPValidationResult(
                ok=False,
                detail=(
                    f"PostHog MCP connected via {config.mode} ({endpoint}) but exposed no tools. "
                    "Check the API key scopes or `features` filter."
                ),
            )
        return PostHogMCPValidationResult(
            ok=True,
            detail=(
                f"PostHog MCP connected via {config.mode} ({endpoint}); "
                f"discovered {len(tool_names)} tool(s)."
            ),
            tool_names=tool_names,
        )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="posthog_mcp",
            method="validate_posthog_mcp_config",
        )
        return PostHogMCPValidationResult(
            ok=False,
            detail=f"PostHog MCP validation failed: {describe_posthog_mcp_error(err, config)}",
        )


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cfg = build_posthog_mcp_config(
            {
                "url": credentials.get("url", ""),
                "mode": credentials.get("mode", "streamable-http"),
                "command": credentials.get("command", ""),
                "args": credentials.get("args", []),
                "auth_token": credentials.get("auth_token", ""),
                "organization_id": credentials.get("organization_id", ""),
                "project_id": credentials.get("project_id", ""),
                "features": credentials.get("features", []),
                "read_only": credentials.get("read_only", True),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="posthog_mcp", record_id=record_id)
        return None, None
    if cfg.is_configured:
        config_dict = cfg.model_dump()
        config_dict["connection_verified"] = True
        return config_dict, "posthog_mcp"
    return None, None
