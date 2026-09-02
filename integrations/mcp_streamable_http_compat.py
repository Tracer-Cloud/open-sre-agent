"""Forward Streamable HTTP MCP transport across ``mcp`` SDK API shapes."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import cache
from importlib import import_module
from typing import Any

import httpx


@cache
def _load_streamable_http_clients() -> tuple[Any, Any]:
    module = import_module("mcp.client.streamable_http")
    modern_client = getattr(module, "streamable_http_client", None)
    legacy_client = getattr(module, "streamablehttp_client", None)
    if modern_client is None and legacy_client is None:
        raise ImportError("mcp.client.streamable_http has no streamable HTTP client")
    return modern_client, legacy_client


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
    modern_client, legacy_client = _load_streamable_http_clients()
    if modern_client is not None:
        del headers, timeout, sse_read_timeout
        async with modern_client(
            url,
            http_client=http_client,
            terminate_on_close=terminate_on_close,
        ) as triple:
            yield triple
        return

    del http_client
    async with legacy_client(
        url,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
        terminate_on_close=terminate_on_close,
    ) as triple:
        yield triple
