"""Forward Streamable HTTP MCP transport across ``mcp`` SDK API shapes."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib import import_module
from typing import Any

import httpx

_streamable_http_module = import_module("mcp.client.streamable_http")
_mcp_streamable_http_client: Any = getattr(_streamable_http_module, "streamable_http_client", None)
_mcp_streamablehttp_client: Any = getattr(_streamable_http_module, "streamablehttp_client", None)

if _mcp_streamable_http_client is None and _mcp_streamablehttp_client is None:
    raise ImportError("mcp.client.streamable_http has no streamable HTTP client")


@asynccontextmanager
async def streamable_http_client(
    url: str,
    *,
    http_client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    sse_read_timeout: float = 300.0,
    terminate_on_close: bool = True,
) -> AsyncGenerator[tuple[Any, Any, Any]]:
    if _mcp_streamable_http_client is not None:
        del headers, timeout, sse_read_timeout
        async with _mcp_streamable_http_client(
            url,
            http_client=http_client,
            terminate_on_close=terminate_on_close,
        ) as triple:
            yield triple
        return

    del http_client
    async with _mcp_streamablehttp_client(
        url,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
        terminate_on_close=terminate_on_close,
    ) as triple:
        yield triple
