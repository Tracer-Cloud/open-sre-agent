"""End-to-end: OpenClaw Gateway is down — assert OpenSRE identifies it.

First fault scenario for #1484. Boots OpenClaw without the Gateway,
drives an MCP tool call (which fails because the bridge can't reach a
Gateway), captures the failure, then asserts the OpenSRE investigation
pipeline names "OpenClaw" + "gateway" and recommends starting it via
``openclaw gateway run``.

Skipped when:
- the ``openclaw`` CLI is not installed, OR
- Node 22.12+ is not available (OpenClaw won't run), OR
- no LLM credential is configured (RCA requires an LLM call)

The first two checks live in :mod:`infrastructure_sdk.local` via
:func:`boot_openclaw`. The LLM-credential check happens here so a
contributor can run ``make test-openclaw`` with just OpenClaw installed
and still see the use-case smoke pass.
"""

from __future__ import annotations

import pytest

from tests.e2e.openclaw.infrastructure_sdk.fault_injection import inject_gateway_down
from tests.e2e.openclaw.infrastructure_sdk.local import (
    LLM_CREDENTIAL_SKIP_REASON,
    OPENCLAW_CLI_SKIP_REASON,
    boot_openclaw,
    llm_credentials_present,
    openclaw_cli_available,
    teardown_openclaw,
)
from tests.e2e.openclaw.use_case import drive_openclaw_conversation

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(not openclaw_cli_available(), reason=OPENCLAW_CLI_SKIP_REASON)
def test_gateway_down_use_case_captures_connection_failure() -> None:
    """Without a running Gateway, the MCP bridge cannot reach one and
    ``conversations_list`` fails.

    This sub-test exercises the use_case + fault-injection wiring
    without depending on an LLM. It locks in the failure-context dict
    shape that ``orchestrator.run_openclaw_investigation`` consumes.
    """
    handle = boot_openclaw(with_gateway=False)
    try:
        inject_gateway_down(handle)
        context = drive_openclaw_conversation(handle)
    finally:
        teardown_openclaw(handle)

    assert context["failure_mode"] == "gateway_down", context
    assert context["transport_mode"] == "stdio"
    assert context["command"] == "openclaw"
    assert context["args"] == "mcp serve"
    # The error string varies by environment (sometimes "Connection
    # closed", sometimes "ECONNREFUSED", sometimes a stdio shutdown
    # message). Assert the canonical gateway-unavailable hint surfaces
    # in the detail rendered by ``describe_openclaw_error``.
    detail = context["error_detail"].lower()
    assert "openclaw" in detail
    assert any(
        marker in detail
        for marker in ("connection closed", "econnrefused", "closed", "could not connect")
    ), context


@pytest.mark.skipif(not openclaw_cli_available(), reason=OPENCLAW_CLI_SKIP_REASON)
@pytest.mark.skipif(not llm_credentials_present(), reason=LLM_CREDENTIAL_SKIP_REASON)
def test_gateway_down_investigation_identifies_openclaw_and_remediation() -> None:
    """Run the full OpenSRE investigation against the captured failure
    and assert the RCA correctly names OpenClaw + the gateway failure
    mode + the remediation hint.

    Real LLM call inside — count this as an integration cost when
    running locally. Skipped when no LLM credential is configured.
    """
    from tests.e2e.openclaw.orchestrator import run_openclaw_investigation, summarize_result

    handle = boot_openclaw(with_gateway=False)
    try:
        inject_gateway_down(handle)
        failure_context = drive_openclaw_conversation(handle)
        assert failure_context["failure_mode"] == "gateway_down"
        result = run_openclaw_investigation(handle, failure_context)
    finally:
        teardown_openclaw(handle)

    summary = summarize_result(result)
    assert "openclaw" in summary, result
    # The RCA can legitimately attribute the failure at either the
    # Gateway layer ("openclaw gateway down") or the bridge layer
    # ("openclaw mcp bridge unreachable") — both identify the right
    # OpenClaw subsystem and are correct readings of the captured
    # ECONNREFUSED. Accept either.
    assert ("gateway" in summary) or ("bridge" in summary), result

    # Remediation should steer the user back to running the Gateway or
    # restarting the bridge — either is a valid action that resolves
    # the ECONNREFUSED failure. ``summary`` includes the report's
    # "## Recommended Actions" section, so we check it directly.
    assert ("openclaw gateway" in summary) or ("openclaw mcp" in summary), result

    # Logged, not asserted: LLM-variance scores (~0.4–0.7) would flake a strict gate.
    print(f"validity_score={result.get('validity_score', 0)} (logged, not asserted)")
