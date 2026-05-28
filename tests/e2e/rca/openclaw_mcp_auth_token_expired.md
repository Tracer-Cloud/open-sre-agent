# Alert: [FIRING:1] OpenClaw MCP Auth Token Rejected — HTTP 401

<!--
  RCA test file — parsed by tests/e2e/rca/run_rca_test.py
  Run via: make test-rca FILE=openclaw_mcp_auth_token_expired

  Scenario: OpenClaw is running in streamable-http mode behind a reverse proxy
  (or the hosted OpenClaw cloud). The OPENCLAW_MCP_AUTH_TOKEN in .env was rotated
  on the OpenClaw side but not updated in opensre. Every MCP call returns HTTP 401.

  To reproduce the alert manually:
    1. Start OpenClaw HTTP bridge: openclaw mcp serve --http --port 9876
    2. Set correct token: export OPENCLAW_MCP_URL=http://localhost:9876/mcp
       export OPENCLAW_MCP_AUTH_TOKEN=valid-token-abc123
    3. Verify it works: opensre integrations verify openclaw
    4. Inject fault: export OPENCLAW_MCP_AUTH_TOKEN=stale-expired-token
    5. Trigger alert: opensre integrations verify openclaw
       → should print: "OpenClaw bridge validation failed: HTTP 401 from POST ..."
    6. Feed alert JSON below to OpenSRE for RCA.

  Required fields in ## Alert Metadata JSON:
    commonLabels.severity      → passed as severity to the agent
    commonLabels.pipeline_name → passed as pipeline_name
-->

## Source
OpenClaw HTTP MCP endpoint (streamable-http transport)

## Message
**Firing**

`opensre integrations verify openclaw` is returning:
```
OpenClaw bridge validation failed: HTTP 401 from POST http://localhost:9876/mcp
```

The bearer token stored in `OPENCLAW_MCP_AUTH_TOKEN` is being rejected.
This affects all opensre investigations that try to read context from OpenClaw
(search_openclaw_conversations, list_openclaw_tools) and all write-back calls
(conversations_create).

Labels:
- alertname = OpenClawMCPAuthTokenExpired
- severity = high
- service = openclaw-mcp-http
- environment = production
- pipeline_name = openclaw_mcp

Annotations:
- error = HTTP 401 from POST http://localhost:9876/mcp
- endpoint = http://localhost:9876/mcp
- transport = streamable-http
- fix = Rotate OPENCLAW_MCP_AUTH_TOKEN in .env to match the new token issued by OpenClaw

## Alert Metadata

```json
{
  "title": "[FIRING:1] OpenClaw MCP Auth Token Rejected — HTTP 401",
  "state": "alerting",
  "commonLabels": {
    "alertname": "OpenClawMCPAuthTokenExpired",
    "severity": "high",
    "service": "openclaw-mcp-http",
    "environment": "production",
    "pipeline_name": "openclaw_mcp"
  },
  "commonAnnotations": {
    "summary": "OpenClaw MCP HTTP endpoint returning 401 Unauthorized. Bearer token has expired or been rotated.",
    "description": "Every MCP call to http://localhost:9876/mcp is returning HTTP 401 Unauthorized. The OPENCLAW_MCP_AUTH_TOKEN stored in the opensre .env does not match the current token on the OpenClaw server. This blocks list_openclaw_tools, search_openclaw_conversations, call_openclaw_bridge_tool, and write-back via conversations_create.",
    "error": "HTTP 401 from POST http://localhost:9876/mcp",
    "endpoint": "http://localhost:9876/mcp",
    "transport": "streamable-http",
    "fault_injection_script": "export OPENCLAW_MCP_AUTH_TOKEN=stale-expired-token",
    "fix": "Update OPENCLAW_MCP_AUTH_TOKEN in .env to the current token from OpenClaw settings"
  },
  "version": "4",
  "groupKey": "{}:{alertname=\"OpenClawMCPAuthTokenExpired\"}",
  "truncatedAlerts": 0,
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "OpenClawMCPAuthTokenExpired",
        "severity": "high",
        "service": "openclaw-mcp-http",
        "environment": "production",
        "instance": "opensre-worker-01"
      },
      "annotations": {
        "summary": "HTTP 401 on OpenClaw MCP endpoint — bearer token rejected",
        "description": "HTTP 401 from POST http://localhost:9876/mcp. Token stale-expired-token rejected.",
        "error": "HTTP 401 from POST http://localhost:9876/mcp"
      },
      "startsAt": "2026-05-11T09:15:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "openclaw-auth-token-expired-001"
    }
  ]
}
```

## Fault Injection Script

The following script reproduces this alert against a real local OpenClaw install:

```bash
#!/usr/bin/env bash
# inject_bad_token.sh — set a stale auth token to trigger 401 from OpenClaw MCP
set -euo pipefail

GOOD_TOKEN="${OPENCLAW_MCP_AUTH_TOKEN:-}"
if [ -z "$GOOD_TOKEN" ]; then
  echo "[inject] ERROR: set OPENCLAW_MCP_AUTH_TOKEN to a valid token first"
  exit 1
fi

echo "[inject] Replacing token with a stale value..."
export OPENCLAW_MCP_AUTH_TOKEN="stale-expired-$(date +%s)"

echo "[inject] Verifying fault is active..."
opensre integrations verify openclaw && echo "ERROR: auth still passing" && exit 1 || true

echo "[inject] Fault confirmed. MCP calls returning 401."
echo "[inject] Run 'opensre investigate' with the alert JSON above to get RCA."
echo "[inject] To restore: export OPENCLAW_MCP_AUTH_TOKEN=$GOOD_TOKEN"
```
