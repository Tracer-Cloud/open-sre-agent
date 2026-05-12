# Alert: [FIRING:1] OpenClaw Write-back Failed — conversations_create Returned Error

<!--
  RCA test file — parsed by tests/e2e/rca/run_rca_test.py
  Run via: make test-rca FILE=openclaw_writeback_mcp_tool_error

  Scenario: OpenClaw is running locally. An investigation completes successfully
  and publish_findings calls send_openclaw_report(). The call reaches OpenClaw's
  MCP bridge, but conversations_create returns is_error=True because OpenClaw
  rejected the request (conversation limit reached, malformed payload, or the
  MCP tool name changed in a newer OpenClaw version).

  The investigation report was delivered to Slack successfully. Only the OpenClaw
  write-back failed — engineers won't see the RCA findings in their AI assistant.

  To reproduce the alert manually:
    1. Start OpenClaw: openclaw gateway start
    2. Configure opensre: export OPENCLAW_MCP_MODE=stdio
       export OPENCLAW_MCP_COMMAND=openclaw
       export OPENCLAW_MCP_ARGS="mcp serve"
    3. Run investigation that produces a write-back:
       opensre investigate -i tests/fixtures/openclaw_test_alert.json
    4. Inject fault: In a patched test environment, mock conversations_create to
       return is_error=True (see fault_injection_script below).
    5. Observe: "[publish] OpenClaw delivery failed: OpenClaw tool call failed."

  Required fields in ## Alert Metadata JSON:
    commonLabels.severity      → passed as severity to the agent
    commonLabels.pipeline_name → passed as pipeline_name
-->

## Source
OpenSRE publish_findings node — post-investigation write-back

## Message
**Firing**

OpenSRE completed an RCA investigation for **Checkout API Error Rate Spike** and attempted
to write the report back to OpenClaw via `conversations_create`, but received:
```
[publish] OpenClaw delivery failed: OpenClaw tool call failed.
```

The investigation report was delivered to Slack successfully. The root cause and remediation
steps are NOT visible in OpenClaw. Engineers who use OpenClaw as their primary AI assistant
will not see the RCA findings and cannot ask follow-up questions about the investigation.

Labels:
- alertname = OpenClawWriteBackFailed
- severity = warning
- service = opensre-publish
- environment = production
- pipeline_name = openclaw_mcp

Annotations:
- mcp_tool = conversations_create
- investigation = Checkout API Error Rate Spike
- error = OpenClaw tool call failed.
- slack_delivery = success

## Alert Metadata

```json
{
  "title": "[FIRING:1] OpenClaw Write-back Failed — conversations_create Returned Error",
  "state": "alerting",
  "commonLabels": {
    "alertname": "OpenClawWriteBackFailed",
    "severity": "warning",
    "service": "opensre-publish",
    "environment": "production",
    "pipeline_name": "openclaw_mcp"
  },
  "commonAnnotations": {
    "summary": "After completing RCA for 'Checkout API Error Rate Spike', send_openclaw_report() called conversations_create on the OpenClaw MCP bridge and received is_error=True. The report was not written to OpenClaw.",
    "description": "publish_findings node called send_openclaw_report(state, slack_message, openclaw_creds). The first attempt used message_send (conversation_id not set, so skipped). The second attempt called conversations_create with title='Checkout API Error Rate Spike' and the full report body. OpenClaw's MCP bridge returned is_error=True with text='OpenClaw tool call failed.' Possible causes: (1) conversations_create tool name changed in a newer OpenClaw version, (2) conversation creation limit reached, (3) malformed content field. Slack delivery completed successfully before this failure. This is a non-fatal failure — the investigation is not re-run.",
    "mcp_tool": "conversations_create",
    "investigation": "Checkout API Error Rate Spike",
    "error": "OpenClaw tool call failed.",
    "slack_delivery": "success",
    "fault_injection_script": "mock conversations_create to return is_error=True",
    "impact": "RCA findings not visible in OpenClaw AI assistant"
  },
  "version": "4",
  "groupKey": "{}:{alertname=\"OpenClawWriteBackFailed\"}",
  "truncatedAlerts": 0,
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "OpenClawWriteBackFailed",
        "severity": "warning",
        "service": "opensre-publish",
        "environment": "production",
        "instance": "opensre-prod-01"
      },
      "annotations": {
        "summary": "conversations_create returned is_error=True — write-back to OpenClaw failed",
        "description": "MCP tool call failed: OpenClaw tool call failed.",
        "error": "OpenClaw tool call failed."
      },
      "startsAt": "2026-05-11T10:05:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "openclaw-writeback-mcp-tool-error-001"
    }
  ]
}
```

## Fault Injection Script

The following script patches the write-back path to return `is_error=True`
and then runs an investigation to trigger the alert:

```bash
#!/usr/bin/env bash
# inject_writeback_error.sh — make conversations_create return is_error=True
set -euo pipefail

echo "[inject] Running investigation with patched conversations_create..."
uv run python - <<'PYEOF'
from unittest.mock import patch

# Patch conversations_create to return is_error=True
def _failing_tool(config, tool_name, arguments):
    if tool_name == "conversations_create":
        return {"is_error": True, "text": "OpenClaw tool call failed."}
    return {"is_error": False, "text": "ok"}

with patch("app.utils.openclaw_delivery.call_openclaw_tool", side_effect=_failing_tool):
    from app.utils.openclaw_delivery import send_openclaw_report
    from unittest.mock import patch as _p
    with _p("app.utils.openclaw_delivery.openclaw_runtime_unavailable_reason", return_value=None):
        state = {
            "alert_name": "Checkout API Error Rate Spike",
            "root_cause": "Database connection pool exhausted under high traffic",
            "remediation_steps": ["Increase max_connections", "Add read replica"],
            "validity_score": 0.93,
            "openclaw_context": {},
        }
        creds = {
            "url": "https://openclaw.example.com/mcp",
            "mode": "streamable-http",
            "auth_token": "tok",
        }
        posted, error = send_openclaw_report(state, "RCA report body", creds)
        print(f"posted={posted}, error={error!r}")
        assert posted is False, "Expected write-back to fail"
        print("[inject] Fault confirmed. Write-back returned (False, error).")
PYEOF
```
