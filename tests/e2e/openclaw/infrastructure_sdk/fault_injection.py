"""Fault injectors for the OpenClaw end-to-end test suite.

Each scenario gets its own injector. They're independent — issues #5
(sleeping tool call → timeout) and #6 (wrong endpoint) can be
implemented in parallel after :func:`inject_gateway_down` (this PR
/ #3) lands.

Each injector takes a previously booted :class:`OpenClawHandle` and
mutates the state so the next ``use_case.drive_openclaw_conversation``
call hits the broken path. Injectors are idempotent — safe to call on
handles that are already in the target state (e.g. ``inject_gateway_down``
on a handle booted with ``with_gateway=False``).
"""

from __future__ import annotations

import sys

from app.integrations.openclaw import OpenClawConfig
from tests.e2e.openclaw.infrastructure_sdk.local import OpenClawHandle, teardown_openclaw

# Port + URL of OpenClaw's Control UI / Gateway service. ``opensre``
# users sometimes mistake this for the MCP bridge endpoint —
# :func:`app.integrations.openclaw._is_probable_openclaw_control_ui_url`
# detects that misconfiguration and the wrong-endpoint scenario asserts
# the hint propagates through to the RCA output.
_CONTROL_UI_URL = "http://127.0.0.1:18789/"

# Short timeout for the sleeping-tool scenario: long enough that a
# healthy fixture would respond, short enough that an unresponsive
# tool fails within seconds. Tests asserting the timeout fires use
# this same value so the expected error is reproducible.
_SLEEPING_TOOL_TIMEOUT_SECONDS = 2.0


def inject_gateway_down(handle: OpenClawHandle) -> None:
    """Ensure the OpenClaw Gateway is **not** running on this handle.

    Tears down the Gateway process if the handle has one. When called on
    a bare handle (booted via ``with_gateway=False``) this is a no-op.
    After this call, any ``openclaw mcp serve`` bridge spawned by an MCP
    client will fail to reach a Gateway — surfacing the
    ``Connection closed`` / ``ECONNREFUSED`` failure mode that
    :func:`app.integrations.openclaw._looks_like_openclaw_gateway_unavailable`
    detects.
    """
    teardown_openclaw(handle)
    # Clear handle fields so downstream callers see an unambiguously
    # "Gateway down" handle even if they re-inspect after the call.
    handle.gateway_pid = None
    handle.extra["fault"] = "gateway_down"


def inject_sleeping_tool_call(handle: OpenClawHandle) -> None:
    """Reconfigure the handle so the use_case driver targets a Python
    stdio MCP fixture whose only tool sleeps instead of returning.

    Used to verify OpenSRE's tool-call timeout behavior — the
    orchestrator must surface a useful "tool timed out" error rather
    than blocking the investigation pipeline indefinitely.
    :func:`app.integrations.openclaw._call_tool_async` wraps
    ``session.call_tool(...)`` with :func:`asyncio.wait_for` so
    ``OpenClawConfig.timeout_seconds`` applies uniformly across all
    transports (stdio / sse / streamable-http).
    """
    fixture_path = "tests.e2e.openclaw.fixtures.sleeping_mcp_server"
    handle.extra["openclaw_config"] = OpenClawConfig(
        mode="stdio",
        command=sys.executable,
        args=("-m", fixture_path),
        timeout_seconds=_SLEEPING_TOOL_TIMEOUT_SECONDS,
        integration_id="openclaw-e2e-sleeping-tool",
    )
    handle.extra["fault"] = "tool_call_timeout"


def inject_wrong_endpoint(handle: OpenClawHandle) -> None:
    """Reconfigure the handle so the use_case driver targets OpenClaw's
    Control UI / Gateway port (18789) instead of the MCP bridge.

    A common user-side misconfiguration: pasting the Gateway URL into
    the MCP integration config. ``validate_openclaw_config`` detects
    this via :func:`app.integrations.openclaw._is_probable_openclaw_control_ui_url`
    and returns the canonical "Use mode `stdio` with command `openclaw`
    and args `mcp serve`" hint. This injector stashes the matching
    misconfigured config on ``handle.extra`` so the use_case driver
    picks it up via :data:`tests.e2e.openclaw.use_case.HANDLE_CONFIG_KEY`.

    No process is spawned and no Gateway interaction happens — the
    failure surfaces from config validation alone, which is the entire
    point of the scenario.
    """
    handle.extra["openclaw_config"] = OpenClawConfig(
        mode="streamable-http",
        url=_CONTROL_UI_URL,
        timeout_seconds=10.0,
        integration_id="openclaw-e2e-wrong-endpoint",
    )
    handle.extra["fault"] = "wrong_endpoint"
