# Alert: [FIRING:1] OpenClaw CLI Binary Not Found — stdio MCP Bridge Broken

<!--
  RCA test file — parsed by tests/e2e/rca/run_rca_test.py
  Run via: make test-rca FILE=openclaw_stdio_command_not_found

  Scenario: OpenClaw is configured for stdio mode. The openclaw binary was
  uninstalled, moved off PATH, or the legacy `openclaw-mcp` command was used
  after a version upgrade that renamed it to `openclaw mcp serve`.
  FileNotFoundError fires on every investigation that tries to use a bridge tool.

  To reproduce the alert manually (legacy command variant):
    1. Set: export OPENCLAW_MCP_MODE=stdio
       export OPENCLAW_MCP_COMMAND=openclaw-mcp   ← deprecated binary
    2. Trigger alert: opensre integrations verify openclaw
       → should print: "Command not found: openclaw-mcp
          Hint: OpenClaw's current MCP bridge is exposed via `openclaw mcp serve`..."
    3. Feed alert JSON below to OpenSRE for RCA.

  To reproduce (binary removed from PATH variant):
    1. Set: export OPENCLAW_MCP_MODE=stdio
       export OPENCLAW_MCP_COMMAND=openclaw
    2. Inject fault: sudo mv $(which openclaw) /tmp/openclaw.bak
    3. Trigger alert: opensre integrations verify openclaw
       → should print: "Command not found: openclaw
          Hint: Install the OpenClaw CLI or set OPENCLAW_MCP_COMMAND to the full path."
    4. To restore: sudo mv /tmp/openclaw.bak $(dirname $(which python))/openclaw

  Required fields in ## Alert Metadata JSON:
    commonLabels.severity      → passed as severity to the agent
    commonLabels.pipeline_name → passed as pipeline_name
-->

## Source
OpenSRE worker (stdio MCP transport — local host)

## Message
**Firing**

`opensre integrations verify openclaw` is returning:
```
OpenClaw bridge validation failed: Command not found: openclaw-mcp
Hint: OpenClaw's current MCP bridge is exposed via `openclaw mcp serve`, not `openclaw-mcp`.
Install the OpenClaw CLI or set `OPENCLAW_MCP_COMMAND` to the full executable path.
```

The opensre worker is configured with `OPENCLAW_MCP_COMMAND=openclaw-mcp` which was the
binary name in OpenClaw ≤ v2.0. Since v2.1.0 the MCP bridge is started via
`openclaw mcp serve`. Every stdio-mode MCP bridge call raises `FileNotFoundError`.

Labels:
- alertname = OpenClawStdioCommandNotFound
- severity = high
- service = opensre-worker
- environment = staging
- pipeline_name = openclaw_mcp

Annotations:
- error = Command not found: openclaw-mcp
- command = openclaw-mcp
- transport = stdio
- fix = Set OPENCLAW_MCP_COMMAND=openclaw and OPENCLAW_MCP_ARGS='mcp serve'

## Alert Metadata

```json
{
  "title": "[FIRING:1] OpenClaw CLI Binary Not Found — stdio MCP Bridge Broken",
  "state": "alerting",
  "commonLabels": {
    "alertname": "OpenClawStdioCommandNotFound",
    "severity": "high",
    "service": "opensre-worker",
    "environment": "staging",
    "pipeline_name": "openclaw_mcp"
  },
  "commonAnnotations": {
    "summary": "opensre cannot find the openclaw-mcp binary. The command was renamed to 'openclaw mcp serve' in v2.1.0.",
    "description": "FileNotFoundError: [Errno 2] No such file or directory: 'openclaw-mcp'. The opensre worker has OPENCLAW_MCP_COMMAND=openclaw-mcp but this binary no longer exists. In OpenClaw v2.1.0 the stdio MCP bridge entrypoint changed from the standalone 'openclaw-mcp' binary to 'openclaw mcp serve'. All three bridge tools are permanently unavailable until the config is corrected.",
    "error": "Command not found: openclaw-mcp",
    "command": "openclaw-mcp",
    "transport": "stdio",
    "openclaw_version_breaking_change": "v2.1.0",
    "fault_injection_script": "export OPENCLAW_MCP_COMMAND=openclaw-mcp",
    "fix": "export OPENCLAW_MCP_COMMAND=openclaw && export OPENCLAW_MCP_ARGS='mcp serve'"
  },
  "version": "4",
  "groupKey": "{}:{alertname=\"OpenClawStdioCommandNotFound\"}",
  "truncatedAlerts": 0,
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "OpenClawStdioCommandNotFound",
        "severity": "high",
        "service": "opensre-worker",
        "environment": "staging",
        "instance": "opensre-worker-staging-02"
      },
      "annotations": {
        "summary": "openclaw-mcp binary not found — legacy command removed in v2.1.0",
        "description": "FileNotFoundError on 'openclaw-mcp'. Correct command is 'openclaw mcp serve'.",
        "error": "Command not found: openclaw-mcp"
      },
      "startsAt": "2026-05-11T07:00:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "openclaw-stdio-command-not-found-001"
    }
  ]
}
```

## Fault Injection Script

The following script reproduces this alert against a real local OpenClaw install:

```bash
#!/usr/bin/env bash
# inject_bad_command.sh — configure the legacy binary name to break stdio mode
set -euo pipefail

SAVED_COMMAND="${OPENCLAW_MCP_COMMAND:-}"
SAVED_ARGS="${OPENCLAW_MCP_ARGS:-}"

echo "[inject] Switching OPENCLAW_MCP_COMMAND to legacy 'openclaw-mcp'..."
export OPENCLAW_MCP_MODE=stdio
export OPENCLAW_MCP_COMMAND=openclaw-mcp
unset OPENCLAW_MCP_ARGS

echo "[inject] Verifying fault is active..."
opensre integrations verify openclaw && echo "ERROR: verify passed unexpectedly" && exit 1 || true

echo "[inject] Fault confirmed. 'Command not found: openclaw-mcp' triggered."
echo "[inject] Run 'opensre investigate' with the alert JSON above to get RCA."
echo "[inject] To restore:"
echo "  export OPENCLAW_MCP_COMMAND=${SAVED_COMMAND:-openclaw}"
echo "  export OPENCLAW_MCP_ARGS='${SAVED_ARGS:-mcp serve}'"
```
