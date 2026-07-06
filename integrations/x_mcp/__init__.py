"""Shared X (Twitter) MCP integration helpers.

X ships an official Model Context Protocol (MCP) server
(https://github.com/xdevplatform/xmcp) that exposes the X API — posting,
search, timelines, likes, retweets, bookmarks, and more — as function-calling
tools. Unlike PostHog/Sentry's always-on hosted MCP servers, XMCP is designed
to run locally (optionally tunneled for remote access): a user clones the
repo, supplies their own X API credentials, and runs the server themselves.
This module centralizes X MCP configuration, validation, and tool-calling so
the onboarding wizard, verify CLI, chat tools, and investigation actions all
share the same transport and parsing logic.

Supported transports:
  - streamable-http  (default) — HTTP-based MCP, typically http://127.0.0.1:8000/mcp
                       or a tunneled URL (e.g. ngrok) for remote access
  - sse              — Server-Sent Events MCP transport
  - stdio            — subprocess-based MCP (opensre launches the local xmcp
                       server directly, e.g. ``python server.py``)

Authentication: XMCP itself authenticates to the X API using its own
X_BEARER_TOKEN environment variable at startup. When opensre launches the
server via ``stdio``, that token is forwarded into the subprocess
environment. For ``streamable-http``/``sse`` connections to an
already-running server, an optional bearer token is sent as an Authorization
header only if configured (useful when the endpoint sits behind an
authenticating tunnel/proxy); XMCP does not itself require one for a trusted
local connection.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Coroutine, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

import httpx
from mcp import ClientSession, StdioServerParameters, types  # type: ignore[import-not-found]
from mcp.client.sse import sse_client  # type: ignore[import-not-found]
from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]
from pydantic import Field, field_validator, model_validator
from typing_extensions import TypedDict

from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure, report_validation_failure
from integrations.mcp_streamable_http_compat import streamable_http_client

logger = logging.getLogger(__name__)

DEFAULT_X_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_X_MCP_MODE: Literal["streamable-http", "sse", "stdio"] = "streamable-http"


class XMCPToolDescriptor(TypedDict):
    """A tool exposed by the X MCP server."""

    name: str
    description: str
    input_schema: object | None


class XMCPContentItem(TypedDict, total=False):
    """Normalized content item returned by an MCP tool call."""

    type: str
    text: str
    uri: str
    mime_type: str


class XMCPToolCallResult(TypedDict, total=False):
    """Normalized response from an X MCP tool call."""

    is_error: bool
    text: str
    content: list[XMCPContentItem]
    structured_content: object | None
    tool: str
    arguments: dict[str, object]


class XMCPConfig(StrictConfigModel):
    """Normalized X MCP connection settings."""

    url: str = DEFAULT_X_MCP_URL
    mode: Literal["stdio", "sse", "streamable-http"] = DEFAULT_X_MCP_MODE
    auth_token: str = ""
    bearer_token: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    headers: dict[str, str] = Field(default_factory=dict)
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
        normalized = str(value or DEFAULT_X_MCP_MODE).strip().lower()
        normalized = normalized or DEFAULT_X_MCP_MODE
        # Generic aliases that callers (env, store, or the planner) may emit
        # all map to the default HTTP transport rather than tripping the
        # Literal validation. "default" is what the planner tends to guess when
        # it has no explicit transport to pass.
        if normalized in {"mcp", "default", "http", "https", "streamable_http"}:
            return DEFAULT_X_MCP_MODE
        return normalized

    @field_validator("auth_token", mode="before")
    @classmethod
    def _normalize_auth_token(cls, value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        return token

    @field_validator("bearer_token", mode="before")
    @classmethod
    def _normalize_bearer_token(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("command", mode="before")
    @classmethod
    def _normalize_command(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("args", mode="before")
    @classmethod
    def _normalize_args(cls, value: object) -> tuple[str, ...]:
        if value is None or not isinstance(value, (list, tuple, set)):
            return ()
        return tuple(str(arg).strip() for arg in value if str(arg).strip())

    @field_validator("headers", mode="before")
    @classmethod
    def _normalize_headers(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}

    @model_validator(mode="after")
    def _validate_transport_requirements(self) -> XMCPConfig:
        if self.mode == "stdio" and not self.command:
            raise ValueError("X MCP mode 'stdio' requires a non-empty command.")
        if self.mode != "stdio" and not self.url:
            raise ValueError(f"X MCP mode '{self.mode}' requires a non-empty url.")
        return self

    @property
    def is_configured(self) -> bool:
        if self.mode == "stdio":
            return bool(self.command)
        return bool(self.url)

    @property
    def request_headers(self) -> dict[str, str]:
        headers = {k: v for k, v in self.headers.items() if v}
        if self.auth_token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    @property
    def subprocess_env(self) -> dict[str, str]:
        """Extra env vars to forward when launching the server ourselves (stdio)."""
        env: dict[str, str] = {}
        if self.bearer_token:
            env["X_BEARER_TOKEN"] = self.bearer_token
        return env


@dataclass(frozen=True)
class XMCPValidationResult:
    """Result of validating an X MCP connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def build_x_mcp_config(raw: Mapping[str, object] | None) -> XMCPConfig:
    """Build a normalized X MCP config object from env/store data."""
    payload = dict(raw or {})
    allowed = set(XMCPConfig.model_fields)
    sanitized = {key: value for key, value in payload.items() if key in allowed}
    return XMCPConfig.model_validate(sanitized)


def x_mcp_config_from_env() -> XMCPConfig | None:
    """Load an X MCP config from environment variables."""
    mode = os.getenv("X_MCP_MODE", DEFAULT_X_MCP_MODE).strip().lower()
    url = os.getenv("X_MCP_URL", "").strip()
    command = os.getenv("X_MCP_COMMAND", "").strip()
    auth_token = os.getenv("X_MCP_AUTH_TOKEN", "").strip()
    bearer_token = os.getenv("X_BEARER_TOKEN", "").strip()
    args_env = os.getenv("X_MCP_ARGS", "").strip()

    mode = mode or DEFAULT_X_MCP_MODE
    if mode == "stdio":
        if not command:
            return None
    else:
        url = url or DEFAULT_X_MCP_URL

    return build_x_mcp_config(
        {
            "url": url,
            "mode": mode,
            "command": command,
            "args": [part for part in args_env.split() if part],
            "auth_token": auth_token,
            "bearer_token": bearer_token,
        }
    )


def x_mcp_runtime_unavailable_reason(config: XMCPConfig) -> str | None:
    """Return a setup error when the config cannot be used."""
    if not config.is_configured:
        return "X MCP is not configured: provide a URL (HTTP/SSE) or command (stdio)."
    if config.mode == "stdio" and not config.bearer_token:
        return (
            "X MCP stdio mode requires an X API bearer token to launch the local server. "
            "Set X_BEARER_TOKEN."
        )
    return None


@asynccontextmanager
async def _open_x_mcp_session(config: XMCPConfig) -> AsyncIterator[ClientSession]:
    """Open an MCP client session for X using the configured transport."""
    stack = AsyncExitStack()
    try:
        if config.mode == "stdio":
            if not config.command:
                raise ValueError(
                    "Invalid X MCP config: mode=stdio requires command "
                    "(set X_MCP_COMMAND or pass command in config)."
                )
            server_params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env={
                    **os.environ,
                    # Suppress terminal control codes so the MCP server's stdout
                    # stays clean JSON-RPC (mirrors integrations/github/mcp.py mitigation).
                    "NO_COLOR": "1",
                    "TERM": "dumb",
                    **config.subprocess_env,
                },
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))

        elif config.mode == "sse":
            if not config.url:
                raise ValueError(
                    "Invalid X MCP config: mode=sse requires url "
                    "(set X_MCP_URL, e.g. http://127.0.0.1:8000/sse)."
                )
            read_stream, write_stream = await stack.enter_async_context(
                sse_client(
                    config.url,
                    headers=config.request_headers,
                    timeout=config.timeout_seconds,
                    sse_read_timeout=max(60.0, config.timeout_seconds),
                )
            )

        elif config.mode == "streamable-http":
            if not config.url:
                raise ValueError(
                    "Invalid X MCP config: mode=streamable-http requires url "
                    "(set X_MCP_URL, e.g. http://127.0.0.1:8000/mcp)."
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
                    config.url,
                    http_client=http_client,
                    headers=config.request_headers,
                    timeout=config.timeout_seconds,
                    sse_read_timeout=read_timeout,
                )
            )

        else:
            raise ValueError(
                f"Unsupported X MCP mode '{config.mode}'. "
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
        return "X MCP tool call timed out"
    return str(exc).strip() or exc.__class__.__name__


def describe_x_mcp_error(err: BaseException, config: XMCPConfig) -> str:
    """Render a human-readable error with a setup hint when useful."""
    detail = _root_cause_message(err)
    hints: list[str] = []

    if isinstance(err, httpx.HTTPStatusError) and err.response.status_code in (401, 403):
        hints.append(
            "Authentication failed. If the endpoint is tunneled behind an "
            "authenticating proxy, set X_MCP_AUTH_TOKEN; otherwise check the "
            "local xmcp server's own X API credentials (X_BEARER_TOKEN)."
        )
    elif isinstance(err, (httpx.ConnectError, httpx.ConnectTimeout)):
        hints.append(
            f"Could not reach {config.url}. Confirm the local xmcp server "
            "(https://github.com/xdevplatform/xmcp) is running and reachable."
        )

    if "timed out" in detail.lower():
        hints.append(
            f"The tool did not return within {config.timeout_seconds:.1f}s. "
            "Raise XMCPConfig.timeout_seconds if the tool is expected to be slow."
        )

    if hints:
        return f"{detail} Hint: {' '.join(hints)}"
    return detail


def _tool_result_to_dict(result: types.CallToolResult) -> XMCPToolCallResult:
    text_parts: list[str] = []
    content_items: list[XMCPContentItem] = []

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


async def _list_tools_async(config: XMCPConfig) -> list[types.Tool]:
    async with _open_x_mcp_session(config) as session:
        result = await session.list_tools()
        return list(result.tools)


def _list_tools_sync(config: XMCPConfig) -> list[types.Tool]:
    # Bound session open (transport connect + MCP `initialize` handshake) and
    # the list_tools RPC together, so a server that accepts a connection but
    # never completes the handshake or responds cannot hang the pipeline.
    return cast(
        list[types.Tool],
        _run_async(asyncio.wait_for(_list_tools_async(config), timeout=config.timeout_seconds)),
    )


def list_x_mcp_tools(config: XMCPConfig) -> list[XMCPToolDescriptor]:
    """List available tools from the configured X MCP server."""
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
    config: XMCPConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> XMCPToolCallResult:
    async with _open_x_mcp_session(config) as session:
        result = await session.call_tool(tool_name, arguments or {})
        payload = _tool_result_to_dict(result)
        payload["tool"] = tool_name
        payload["arguments"] = arguments or {}
        return payload


def call_x_mcp_tool(
    config: XMCPConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> XMCPToolCallResult:
    """Call an X MCP tool and normalize the result."""
    # Bound session open (transport connect + MCP `initialize` handshake) and
    # the call_tool RPC together, so a server that accepts a connection but
    # never completes the handshake or responds cannot hang the pipeline.
    return cast(
        XMCPToolCallResult,
        _run_async(
            asyncio.wait_for(
                _call_tool_async(config, tool_name, arguments), timeout=config.timeout_seconds
            )
        ),
    )


def validate_x_mcp_config(config: XMCPConfig) -> XMCPValidationResult:
    """Validate X MCP connectivity by listing available tools."""
    runtime_error = x_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return XMCPValidationResult(
            ok=False,
            detail=f"X MCP validation failed: {runtime_error}",
        )

    try:
        tools = list_x_mcp_tools(config)
        tool_names = tuple(sorted(t["name"] for t in tools))
        endpoint = config.command if config.mode == "stdio" else config.url
        if not tool_names:
            return XMCPValidationResult(
                ok=False,
                detail=(
                    f"X MCP connected via {config.mode} ({endpoint}) but exposed no tools. "
                    "Check the server's X API credentials or tool allowlist."
                ),
            )
        return XMCPValidationResult(
            ok=True,
            detail=(
                f"X MCP connected via {config.mode} ({endpoint}); "
                f"discovered {len(tool_names)} tool(s)."
            ),
            tool_names=tool_names,
        )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="x_mcp",
            method="validate_x_mcp_config",
        )
        return XMCPValidationResult(
            ok=False,
            detail=f"X MCP validation failed: {describe_x_mcp_error(err, config)}",
        )


def classify(credentials: dict[str, Any], record_id: str) -> tuple[XMCPConfig | None, str | None]:
    try:
        cfg = build_x_mcp_config(
            {
                "url": credentials.get("url", ""),
                "mode": credentials.get("mode", "streamable-http"),
                "command": credentials.get("command", ""),
                "args": credentials.get("args", []),
                "auth_token": credentials.get("auth_token", ""),
                "bearer_token": credentials.get("bearer_token", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="x_mcp", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "x_mcp"
    return None, None
