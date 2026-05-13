"""Minimal stdio MCP server whose only tool sleeps instead of returning.

Used by the OpenClaw tool-call-timeout e2e scenario to exercise
``call_openclaw_tool`` against a tool that never responds — the test
verifies OpenSRE surfaces a timeout error rather than blocking the
whole investigation pipeline. Run as a subprocess via
``python -m tests.e2e.openclaw.fixtures.sleeping_mcp_server`` (this is
how :func:`tests.e2e.openclaw.infrastructure_sdk.fault_injection.inject_sleeping_tool_call`
spawns it under stdio transport).

The tool ``conversations_list`` is used (same name OpenClaw exposes)
so the use_case driver doesn't need to know it's hitting a fixture
instead of the real bridge — the test just swaps the config.
"""

from __future__ import annotations

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_SERVER_NAME = "sleeping-mcp-fixture"
# 1 hour. Effectively "never returns" for any reasonable test timeout
# but bounded so a misbehaving test runner doesn't leak this process
# into a CI worker for the full job duration.
_SLEEP_SECONDS = 3600.0


def _build_server() -> Server[object]:
    server: Server[object] = Server(_SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="conversations_list",
                description=(
                    "Fixture tool that intentionally sleeps instead of returning. "
                    "Used to exercise OpenSRE's call-timeout behavior."
                ),
                inputSchema={"type": "object", "properties": {}, "additionalProperties": True},
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, object]) -> list[TextContent]:
        # Sleep deliberately past any sensible test timeout. ``arguments``
        # and ``name`` are intentionally ignored so the test can pass any
        # shape without affecting sleep behavior.
        del name, arguments
        await asyncio.sleep(_SLEEP_SECONDS)
        return [TextContent(type="text", text="(unreachable)")]

    return server


async def _main() -> None:
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
