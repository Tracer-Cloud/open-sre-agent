# Alert: [FIRING:1] OpenClaw Gateway Unavailable — MCP Bridge Down

<!--
  RCA test file — parsed by tests/e2e/rca/run_rca_test.py
  Run via: make test-rca FILE=openclaw_gateway_crashed

  Scenario: OpenClaw is installed locally. A fault-injection script stops the
  gateway process (`openclaw gateway stop` or `pkill -f "openclaw gateway"`).
  All subsequent `opensre integrations verify openclaw` calls return
  "Connection closed" and MCP bridge tools become unreachable.

  To reproduce the alert manually:
    1. Start OpenClaw: openclaw gateway start
    2. Verify it works: opensre integrations verify openclaw
    3. Inject fault:    openclaw gateway stop   (or: pkill -f "openclaw gateway")
    4. Trigger alert:   opensre integrations verify openclaw
       → should print: "Connection closed. Hint: Check `openclaw gateway status`..."
    5. Feed alert JSON below to OpenSRE for RCA.

  Required fields in ## Alert Metadata JSON:
    commonLabels.severity      → passed as severity to the agent
    commonLabels.pipeline_name → passed as pipeline_name
-->

## Source
OpenClaw local host (stdio MCP transport)

## Message
**Firing**

The OpenClaw MCP gateway process has stopped responding on the host machine.
`opensre integrations verify openclaw` is returning:
```
OpenClaw bridge validation failed: Connection closed.
Hint: The `openclaw mcp serve` bridge needs a running OpenClaw Gateway.
Check `openclaw gateway status`, then start it with `openclaw gateway run`
(foreground) or `openclaw gateway install` followed by `openclaw gateway start`.
```

All three bridge tools (`list_openclaw_tools`, `search_openclaw_conversations`,
`call_openclaw_bridge_tool`) are permanently unavailable for the duration of the outage.
Investigation write-back to OpenClaw is also failing.

Labels:
- alertname = OpenClawGatewayUnavailable
- severity = critical
- service = openclaw-gateway
- environment = local
- pipeline_name = openclaw_mcp

Annotations:
- error = Connection closed
- command = openclaw mcp serve
- last_successful_heartbeat = 2026-05-11T08:34:00Z

## Alert Metadata

```json
{
  "title": "[FIRING:1] OpenClaw Gateway Unavailable — MCP Bridge Down",
  "state": "alerting",
  "commonLabels": {
    "alertname": "OpenClawGatewayUnavailable",
    "severity": "critical",
    "service": "openclaw-gateway",
    "environment": "local",
    "pipeline_name": "openclaw_mcp"
  },
  "commonAnnotations": {
    "summary": "OpenClaw MCP gateway process is not running. All MCP bridge calls returning 'Connection closed'.",
    "description": "The openclaw gateway process stopped responding at 08:42 UTC. opensre tried to connect via 'openclaw mcp serve' stdio transport and received 'Connection closed' on every attempt. Last successful heartbeat was 8 minutes ago. Engineers cannot use OpenClaw's AI assistant and no new MCP sessions can be established.",
    "error": "Connection closed",
    "command": "openclaw mcp serve",
    "transport": "stdio",
    "last_successful_heartbeat": "2026-05-11T08:34:00Z",
    "fault_injection_script": "openclaw gateway stop",
    "fix": "openclaw gateway start"
  },
  "version": "4",
  "groupKey": "{}:{alertname=\"OpenClawGatewayUnavailable\"}",
  "truncatedAlerts": 0,
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "OpenClawGatewayUnavailable",
        "severity": "critical",
        "service": "openclaw-gateway",
        "environment": "local",
        "instance": "openclaw-host-prod-01:18789"
      },
      "annotations": {
        "summary": "OpenClaw MCP gateway process is not running",
        "description": "Connection closed when attempting 'openclaw mcp serve' stdio transport.",
        "error": "Connection closed"
      },
      "startsAt": "2026-05-11T08:42:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "openclaw-gateway-crashed-001"
    }
  ]
}
```

## Fault Injection Script

The following script reproduces this alert against a real local OpenClaw install:

```bash
#!/usr/bin/env bash
# inject_gateway_crash.sh — stop the OpenClaw gateway to trigger the alert
set -euo pipefail

echo "[inject] Stopping OpenClaw gateway..."
openclaw gateway stop 2>/dev/null || pkill -f "openclaw gateway" 2>/dev/null || true

echo "[inject] Verifying fault is active..."
sleep 1
opensre integrations verify openclaw && echo "ERROR: gateway still up" && exit 1 || true

echo "[inject] Fault confirmed. Gateway is down."
echo "[inject] Run 'opensre investigate' with the alert JSON above to get RCA."
echo "[inject] To restore: openclaw gateway start"
```
