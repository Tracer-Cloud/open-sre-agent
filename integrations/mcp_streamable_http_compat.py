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


def _no_session_id() -> str | None:
    """Stand in for the session-id getter on SDKs that do not yield one."""
    return None


def _as_triple(yielded: Any) -> tuple[Any, Any, Any]:
    """Normalize the transport's yield to ``(read, write, get_session_id)``.

    The SDK yields two streams on some versions and three values on others, and
    the arity does not track the function name: ``mcp`` 1.x yields the triple
    from ``streamable_http_client`` while 2.x yields only the two streams from
    the same name. Every caller unpacks three, so an unnormalized yield fails at
    ``ValueError: not enough values to unpack`` on the first connection attempt
    — at runtime, per integration, with no import-time signal.

    Callers that need the session id must tolerate ``None``; no version of the
    SDK reports one before the session is initialized anyway.
    """
    read_stream, write_stream, *rest = yielded
    return read_stream, write_stream, (rest[0] if rest else _no_session_id)


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
        ) as yielded:
            yield _as_triple(yielded)
        return

    del http_client
    async with _mcp_streamablehttp_client(
        url,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
        terminate_on_close=terminate_on_close,
    ) as yielded:
        yield _as_triple(yielded)
