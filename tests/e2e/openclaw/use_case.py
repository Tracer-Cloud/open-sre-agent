"""Drive an OpenClaw conversation against a handle, capture the failure.

Pure business logic: no pytest, no fixtures, no fault injection. Given
an :class:`OpenClawHandle`, builds an :class:`OpenClawConfig` for the
stdio MCP bridge, attempts to call an OpenClaw MCP tool, and returns a
context dict shaped for :func:`orchestrator.run_openclaw_investigation`.

Used by every fault scenario test — gateway-down, tool-call-timeout,
and wrong-endpoint all share the same "call a tool, capture what fires"
pattern. The fault is set up beforehand by ``fault_injection.inject_*``;
this function just observes the resulting failure.
"""

from __future__ import annotations

from typing import Any

from app.integrations.openclaw import (
    OpenClawConfig,
    call_openclaw_tool,
    describe_openclaw_error,
    validate_openclaw_config,
)
from tests.e2e.openclaw.infrastructure_sdk.local import OpenClawHandle

# Tool we call to exercise the bridge. ``conversations_list`` is the
# OpenClaw MCP tool name (``search_openclaw_conversations`` is the
# OpenSRE-side wrapper name in
# :mod:`app.tools.OpenClawMCPTool`). We hit it raw here so the test
# exercises the same code path as a real investigation, without going
# through the agent's tool selection.
_PROBE_TOOL = "conversations_list"

# Fault injectors stash an :class:`OpenClawConfig` override here when a
# scenario needs to point the use_case driver at something other than
# the default stdio bridge (e.g. ``inject_wrong_endpoint`` overrides
# the transport and URL). The use_case reads the override and falls
# back to ``_build_stdio_config`` when absent.
HANDLE_CONFIG_KEY = "openclaw_config"


def _build_stdio_config() -> OpenClawConfig:
    """Build the canonical stdio-transport config that drives the
    bridge subprocess.

    The bridge inherits the parent's PATH and config dir, so a
    contributor running this test with their normal ``~/.openclaw/``
    config gets a bridge that tries to talk to whatever Gateway URL
    that config names. For the gateway-down scenario we just need the
    bridge to fail; it doesn't matter which Gateway address it tries.
    """
    return OpenClawConfig(
        mode="stdio",
        command="openclaw",
        args=("mcp", "serve"),
        # Short timeout so a tool-call-timeout test can fail fast in
        # the follow-up scenario (#5). Gateway-down typically fails
        # within 1-2s on its own (Connection closed) so this is well
        # above what the failure path needs.
        timeout_seconds=10.0,
        integration_id="openclaw-e2e",
    )


def _resolve_config(handle: OpenClawHandle) -> OpenClawConfig:
    """Return the per-scenario config override if a fault injector
    stashed one, otherwise the canonical stdio bridge config.

    The override pattern keeps the use_case interface uniform — every
    fault scenario calls ``drive_openclaw_conversation(handle)`` and
    the per-fault wiring lives on the handle.
    """
    override = handle.extra.get(HANDLE_CONFIG_KEY)
    if isinstance(override, OpenClawConfig):
        return override
    return _build_stdio_config()


def drive_openclaw_conversation(handle: OpenClawHandle) -> dict[str, Any]:
    """Run a single OpenClaw MCP tool call against ``handle``, return
    the captured failure context.

    Returns a dict shaped for :func:`orchestrator.run_openclaw_investigation`:

        {
          "tool": "conversations_list",
          "transport_mode": "stdio",
          "command": "openclaw",
          "args": "mcp serve",
          "gateway_url": "ws://127.0.0.1:19001" | None,
          "last_error": "<one-line summary>",
          "error_detail": "<full describe_openclaw_error output>",
          "failure_mode": "gateway_down" | "tool_call_timeout" | "wrong_endpoint" | "unknown",
        }

    On the happy path (no fault injected, Gateway healthy) the dict has
    ``"failure_mode": "no_failure"`` and the assertions in the scenario
    test should fail explicitly so the test author sees the missing
    fault setup rather than a passing "RCA correctly identified
    nothing" run.
    """
    config = _resolve_config(handle)
    failure_mode = _infer_failure_mode(handle)
    base_context: dict[str, Any] = {
        "tool": _PROBE_TOOL,
        "transport_mode": config.mode,
        "command": config.command,
        "args": " ".join(config.args),
        "url": config.url,
        "gateway_url": handle.gateway_url,
        "failure_mode": failure_mode,
    }

    # Pre-flight config validation. ``validate_openclaw_config`` catches
    # the Control-UI-vs-MCP-bridge misconfiguration (and similar) before
    # we attempt a tool call, surfacing the same hint a user would see
    # from ``opensre integrations verify``. For stdio configs this is a
    # pass-through.
    validation = validate_openclaw_config(config)
    if not validation.ok:
        base_context["last_error"] = (validation.detail or "config invalid")[:200]
        base_context["error_detail"] = validation.detail or "config invalid"
        return base_context

    try:
        result = call_openclaw_tool(config, _PROBE_TOOL, {})
    except Exception as err:  # noqa: BLE001 — capture operational failures; KeyboardInterrupt/SystemExit propagate
        base_context["last_error"] = (
            str(err).splitlines()[0][:200] if str(err) else type(err).__name__
        )
        base_context["error_detail"] = describe_openclaw_error(err, config)
        return base_context

    # No exception — either the Gateway was actually up (test setup bug)
    # or the MCP tool returned an ``is_error`` payload. The bridge maps
    # most gateway failures to ``is_error=True`` rather than letting them
    # raise, so we have to re-run them through ``describe_openclaw_error``
    # ourselves to get the canonical hint surface (otherwise the
    # orchestrator's alert would carry just the raw "ECONNREFUSED 127.0.0.1"
    # text without the "start `openclaw gateway run`" remediation).
    if result.get("is_error"):
        error_text = str(result.get("text", "") or "")
        # ``describe_openclaw_error`` wants an exception; synthesize one
        # carrying the same message so the indicator-matching logic in
        # ``_looks_like_openclaw_gateway_unavailable`` still fires.
        synthetic_error = RuntimeError(error_text)
        base_context["last_error"] = error_text.splitlines()[0][:200]
        base_context["error_detail"] = describe_openclaw_error(synthetic_error, config)
        return base_context

    base_context["failure_mode"] = "no_failure"
    base_context["last_error"] = ""
    base_context["error_detail"] = (
        "OpenClaw MCP tool call succeeded — no fault was active. "
        "Did the fault injector run? Was the Gateway accidentally up?"
    )
    return base_context


def _infer_failure_mode(handle: OpenClawHandle) -> str:
    """Tag the failure mode based on handle state so the orchestrator
    can build a more targeted alert annotation.

    Fault injectors stash a ``"fault"`` key in ``handle.extra`` to be
    explicit about which scenario set up the handle. Falls back to a
    "Gateway absent ⇒ gateway_down" inference for backward-compat with
    handles booted via ``boot_openclaw(with_gateway=False)`` directly.
    """
    explicit = handle.extra.get("fault")
    if isinstance(explicit, str) and explicit:
        return explicit
    if handle.gateway_pid is None:
        return "gateway_down"
    return "unknown"
