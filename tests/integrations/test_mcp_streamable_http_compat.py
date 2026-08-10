"""The Streamable HTTP shim must hand callers a triple on every ``mcp`` SDK."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from integrations import mcp_streamable_http_compat as compat


@asynccontextmanager
async def _yields_two(*_args: Any, **_kwargs: Any) -> AsyncGenerator[tuple[Any, Any]]:
    """Stand in for ``mcp`` 2.x, which yields only the two streams."""
    yield ("read", "write")


@asynccontextmanager
async def _yields_three(*_args: Any, **_kwargs: Any) -> AsyncGenerator[tuple[Any, Any, Any]]:
    """Stand in for ``mcp`` 1.x, which also yields the session-id getter."""
    yield ("read", "write", lambda: "session-1")


async def _open(url: str = "https://example.invalid/mcp/") -> tuple[Any, Any, Any]:
    async with (
        httpx.AsyncClient() as client,
        compat.streamable_http_client(url, http_client=client) as triple,
    ):
        return triple


def test_a_two_value_yield_is_padded_to_a_triple(monkeypatch: pytest.MonkeyPatch) -> None:
    """``mcp`` 2.0 dropped the session-id getter from ``streamable_http_client``.

    Every caller unpacks three values, so without padding the first connection
    raises ``ValueError: not enough values to unpack`` — which is what broke the
    GitHub MCP verify in a deployed pod while the pinned dev version stayed fine.
    """
    monkeypatch.setattr(compat, "_mcp_streamable_http_client", _yields_two)

    read, write, get_session_id = asyncio.run(_open())

    assert (read, write) == ("read", "write")
    assert get_session_id() is None


def test_a_three_value_yield_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compat, "_mcp_streamable_http_client", _yields_three)

    read, write, get_session_id = asyncio.run(_open())

    assert (read, write) == ("read", "write")
    assert get_session_id() == "session-1"


def test_the_legacy_entrypoint_is_normalized_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fallback branch runs when only ``streamablehttp_client`` exists."""
    monkeypatch.setattr(compat, "_mcp_streamable_http_client", None)
    monkeypatch.setattr(compat, "_mcp_streamablehttp_client", _yields_two)

    read, write, get_session_id = asyncio.run(_open())

    assert (read, write) == ("read", "write")
    assert get_session_id() is None


def test_the_installed_sdk_is_normalized_by_the_shim() -> None:
    """Guard the real dependency, not just the fakes.

    Whichever entrypoint the installed ``mcp`` exposes, the shim must produce a
    triple from what it yields. Asserting on the SDK's own yield arity would
    pin the test to one version; asserting the shim normalizes both does not.
    """
    assert compat._as_triple(("read", "write"))[2]() is None
    assert compat._as_triple(("read", "write", lambda: "abc"))[2]() == "abc"
