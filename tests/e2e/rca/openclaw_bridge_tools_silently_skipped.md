# Alert: [FIRING:1] OpenClaw Bridge Tools Never Selected — connection_verified Bug

<!--
  RCA test file — parsed by tests/e2e/rca/run_rca_test.py
  Run via: make test-rca FILE=openclaw_bridge_tools_silently_skipped

  Scenario: OpenClaw is correctly configured and connected. The gateway is running,
  the token is valid, `opensre integrations verify openclaw` passes. Yet during
  every investigation the planner never selects list_openclaw_tools,
  search_openclaw_conversations, or call_openclaw_bridge_tool. Engineers notice
  that OpenClaw conversation context is never used even for services that have
  recent conversation history.

  Root cause (Bug 1 from integration plan):
    _catalog_impl.py returns openclaw_config.model_dump() without injecting
    connection_verified=True. _openclaw_available() reads
    sources["openclaw"]["connection_verified"] which is absent, so all three
    bridge tools report is_available=False permanently.

  To reproduce:
    1. Configure openclaw: export OPENCLAW_MCP_MODE=stdio
       export OPENCLAW_MCP_COMMAND=openclaw
       export OPENCLAW_MCP_ARGS="mcp serve"
    2. Verify passes: opensre integrations verify openclaw  → "discovered N tool(s)"
    3. Run investigation: opensre investigate -i tests/fixtures/openclaw_test_alert.json
    4. Check verbose output: none of the three bridge tools appear in planned_actions
    5. This alert fires when monitoring detects openclaw_tools_selected_total == 0
       across multiple investigations where openclaw was configured.

  Required fields in ## Alert Metadata JSON:
    commonLabels.severity      → passed as severity to the agent
    commonLabels.pipeline_name → passed as pipeline_name
-->

## Source
OpenSRE planner telemetry

## Message
**Firing**

OpenClaw is configured and `opensre integrations verify openclaw` returns success, but
the investigation planner has not selected any OpenClaw bridge tool across the last 47
investigations. The tools `list_openclaw_tools`, `search_openclaw_conversations`, and
`call_openclaw_bridge_tool` all report `is_available=False` despite a valid connection.

This is caused by Bug 1 in the OpenClaw catalog classifier:
`app/integrations/_catalog_impl.py` returns `openclaw_config.model_dump()` without
injecting `connection_verified=True`. The `_openclaw_available()` gate reads
`sources["openclaw"]["connection_verified"]` which is never set, so
`is_available()` always returns `False`.

Labels:
- alertname = OpenClawBridgeToolsNeverSelected
- severity = high
- service = opensre-planner
- environment = production
- pipeline_name = openclaw_mcp

Annotations:
- affected_tools = list_openclaw_tools, search_openclaw_conversations, call_openclaw_bridge_tool
- root_cause_file = app/integrations/_catalog_impl.py
- bug = connection_verified key missing from catalog-resolved openclaw config dict

## Alert Metadata

```json
{
  "title": "[FIRING:1] OpenClaw Bridge Tools Never Selected — connection_verified Bug",
  "state": "alerting",
  "commonLabels": {
    "alertname": "OpenClawBridgeToolsNeverSelected",
    "severity": "high",
    "service": "opensre-planner",
    "environment": "production",
    "pipeline_name": "openclaw_mcp"
  },
  "commonAnnotations": {
    "summary": "OpenClaw bridge tools permanently unavailable despite valid connection. connection_verified key absent from catalog-resolved config dict.",
    "description": "opensre integrations verify openclaw succeeds and the gateway is running, but across 47 consecutive investigations zero OpenClaw bridge tools were selected. Root cause: _catalog_impl.py calls openclaw_config.model_dump() and returns the result directly. model_dump() produces keys url, mode, auth_token, command, args — it does NOT include connection_verified. The _openclaw_available() function reads sources['openclaw']['connection_verified'] which is always absent, so is_available() returns False for all three bridge tools on every single investigation. Fix: add config_dict['connection_verified'] = True after model_dump() in _classify_service_instance() and in the env-var loader path.",
    "affected_tools": "list_openclaw_tools, search_openclaw_conversations, call_openclaw_bridge_tool",
    "root_cause_file": "app/integrations/_catalog_impl.py",
    "bug_id": "Bug 1 (openclaw catalog omits connection_verified)",
    "investigations_affected": "47",
    "fix": "config_dict['connection_verified'] = True after openclaw_config.model_dump()"
  },
  "version": "4",
  "groupKey": "{}:{alertname=\"OpenClawBridgeToolsNeverSelected\"}",
  "truncatedAlerts": 0,
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "OpenClawBridgeToolsNeverSelected",
        "severity": "high",
        "service": "opensre-planner",
        "environment": "production",
        "instance": "opensre-prod-01"
      },
      "annotations": {
        "summary": "OpenClaw bridge tools blocked by missing connection_verified flag in catalog",
        "description": "is_available() always False because connection_verified not injected by _catalog_impl.py",
        "bug": "connection_verified key missing from catalog-resolved openclaw config dict"
      },
      "startsAt": "2026-05-11T06:00:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "openclaw-bridge-tools-silently-skipped-001"
    }
  ]
}
```

## Fault Injection Script

This bug is reproducible in the current codebase without any runtime changes.
The following script confirms whether the bug is present:

```bash
#!/usr/bin/env bash
# check_connection_verified_bug.sh — verify Bug 1 is fixed
set -euo pipefail

echo "[check] Testing connection_verified injection in catalog classifier..."
uv run python - <<'PYEOF'
from app.nodes.resolve_integrations.node import _classify_integrations

record = {
    "id": "openclaw-test",
    "service": "openclaw",
    "status": "active",
    "credentials": {
        "mode": "stdio",
        "command": "openclaw",
        "args": ["mcp", "serve"],
    },
}
resolved = _classify_integrations([record])
openclaw = resolved.get("openclaw", {})
if openclaw.get("connection_verified") is True:
    print("PASS: connection_verified=True is present in catalog-resolved config")
else:
    print(f"FAIL: connection_verified missing. Keys present: {list(openclaw.keys())}")
    print("Bug 1 is still present. Fix: _catalog_impl.py must inject connection_verified=True")
    raise SystemExit(1)
PYEOF
```
