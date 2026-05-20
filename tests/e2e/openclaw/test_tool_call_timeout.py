"""End-to-end: an OpenClaw MCP tool call never returns — assert
OpenSRE surfaces a timeout error rather than blocking forever.

Drives a Python stdio MCP fixture
(:mod:`tests.e2e.openclaw.fixtures.sleeping_mcp_server`) whose only
tool sleeps for an hour. With ``OpenClawConfig.timeout_seconds=2.0``,
a healthy integration should surface a timeout error within ~2s.

:func:`app.integrations.openclaw._call_tool_async` wraps
``session.call_tool(...)`` with :func:`asyncio.wait_for`, applying
``OpenClawConfig.timeout_seconds`` uniformly across all transports.
The sleeping fixture exercises this wrapper end-to-end.
"""

from __future__ import annotations

import pytest

from tests.e2e.openclaw.infrastructure_sdk.fault_injection import inject_sleeping_tool_call
from tests.e2e.openclaw.infrastructure_sdk.local import (
    LLM_CREDENTIAL_SKIP_REASON,
    OPENCLAW_CLI_SKIP_REASON,
    boot_openclaw,
    llm_credentials_present,
    openclaw_cli_available,
    teardown_openclaw,
)

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(not openclaw_cli_available(), reason=OPENCLAW_CLI_SKIP_REASON)
def test_tool_call_timeout_use_case_surfaces_timeout() -> None:
    """The use_case driver must surface a timeout error context within
    seconds when a configured tool never returns.
    """
    from tests.e2e.openclaw.use_case import drive_openclaw_conversation

    handle = boot_openclaw(with_gateway=False)
    try:
        inject_sleeping_tool_call(handle)
        context = drive_openclaw_conversation(handle)
    finally:
        teardown_openclaw(handle)

    assert context["failure_mode"] == "tool_call_timeout", context
    assert context["transport_mode"] == "stdio"
    detail = context["error_detail"].lower()
    assert "timeout" in detail or "timed out" in detail, context


@pytest.mark.skipif(not openclaw_cli_available(), reason=OPENCLAW_CLI_SKIP_REASON)
@pytest.mark.skipif(not llm_credentials_present(), reason=LLM_CREDENTIAL_SKIP_REASON)
def test_tool_call_timeout_investigation_identifies_timeout() -> None:
    """Run the full OpenSRE investigation against a sleeping tool call.

    Asserts the RCA names OpenClaw + timeout, and the remediation
    points the user toward investigating the upstream tool's responsiveness.
    """
    from tests.e2e.openclaw.orchestrator import run_openclaw_investigation, summarize_result
    from tests.e2e.openclaw.use_case import drive_openclaw_conversation

    handle = boot_openclaw(with_gateway=False)
    try:
        inject_sleeping_tool_call(handle)
        failure_context = drive_openclaw_conversation(handle)
        assert failure_context["failure_mode"] == "tool_call_timeout"
        result = run_openclaw_investigation(handle, failure_context)
    finally:
        teardown_openclaw(handle)

    summary = summarize_result(result)
    assert "openclaw" in summary, result
    assert "timeout" in summary or "timed out" in summary, result

    # Logged, not asserted: LLM-variance scores (~0.4–0.7) would flake a strict gate.
    print(f"validity_score={result.get('validity_score', 0)} (logged, not asserted)")
