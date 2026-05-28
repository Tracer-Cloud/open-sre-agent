"""End-to-end: OpenClaw is configured with the wrong endpoint.

A common user-side misconfiguration is pasting the Control UI URL
(``http://127.0.0.1:18789/``) into the MCP integration config instead
of using the ``stdio`` transport.
:func:`app.integrations.openclaw._is_probable_openclaw_control_ui_url`
already detects this; this test asserts the canonical "Use mode
`stdio`" hint propagates through use_case → orchestrator → RCA.

No live Gateway or MCP bridge is needed — the failure surfaces from
``validate_openclaw_config`` alone. So unlike ``test_gateway_down``,
this scenario doesn't even need ``openclaw`` on PATH for the use_case
sub-test (it skips just to keep the suite uniform across scenarios).
"""

from __future__ import annotations

import pytest

from tests.e2e.openclaw.infrastructure_sdk.fault_injection import inject_wrong_endpoint
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
def test_wrong_endpoint_use_case_captures_validation_hint() -> None:
    """Misconfigured streamable-http URL targeting the Control UI port
    must fail ``validate_openclaw_config`` with the canonical "use mode
    `stdio`" hint.

    Exercises the use_case + fault-injection wiring without depending
    on an LLM. Locks in the failure-context dict shape that the
    orchestrator consumes for the wrong-endpoint scenario.
    """
    handle = boot_openclaw(with_gateway=False)
    try:
        inject_wrong_endpoint(handle)
        context = drive_openclaw_conversation(handle)
    finally:
        teardown_openclaw(handle)

    assert context["failure_mode"] == "wrong_endpoint", context
    assert context["transport_mode"] == "streamable-http"
    assert context["url"] == "http://127.0.0.1:18789"  # config normalizes trailing slash
    # The validation message must contain BOTH the Control-UI flag
    # ("Control UI") and the actionable remediation (the stdio hint).
    detail = context["error_detail"].lower()
    assert "control ui" in detail, context
    assert "stdio" in detail, context
    assert "openclaw" in detail, context


@pytest.mark.skipif(not openclaw_cli_available(), reason=OPENCLAW_CLI_SKIP_REASON)
@pytest.mark.skipif(not llm_credentials_present(), reason=LLM_CREDENTIAL_SKIP_REASON)
def test_wrong_endpoint_investigation_steers_user_to_stdio() -> None:
    """Run the full OpenSRE investigation against the captured
    misconfiguration. Asserts the RCA names OpenClaw + flags the
    Control-UI mistake + recommends switching to the stdio bridge.

    Real LLM call inside — count this as an integration cost when
    running locally. Skipped when no LLM credential is configured.
    """
    from tests.e2e.openclaw.orchestrator import run_openclaw_investigation, summarize_result

    handle = boot_openclaw(with_gateway=False)
    try:
        inject_wrong_endpoint(handle)
        failure_context = drive_openclaw_conversation(handle)
        assert failure_context["failure_mode"] == "wrong_endpoint"
        result = run_openclaw_investigation(handle, failure_context)
    finally:
        teardown_openclaw(handle)

    summary = summarize_result(result)
    assert "openclaw" in summary, result
    # The RCA should call out either the Control UI mistake or the
    # stdio remediation — we accept either as evidence the misconfig
    # hint propagated through the investigation surface. ``summary``
    # includes the report's "## Recommended Actions" section.
    assert ("control ui" in summary) or ("stdio" in summary), result

    # Logged, not asserted: LLM-variance scores (~0.4–0.7) would flake a strict gate.
    print(f"validity_score={result.get('validity_score', 0)} (logged, not asserted)")
