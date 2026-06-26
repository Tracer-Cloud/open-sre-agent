#!/usr/bin/env bash
# infra/scripts/signoz/test-query-api.sh
# Live smoke test for SigNoz Query API integration (logs, metrics, traces).
# Requires a running SigNoz stack and SIGNOZ_API_KEY.
#
# Usage (from repo root):
#   export SIGNOZ_API_KEY="<service-account-key>"
#   bash infra/scripts/signoz/test-query-api.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=infra/scripts/signoz/env.sh
source "$SCRIPT_DIR/env.sh"

if [[ -z "${SIGNOZ_API_KEY:-}" ]]; then
  echo "SIGNOZ_API_KEY is required. Create one in SigNoz: Settings → Service Accounts → Keys."
  exit 1
fi

echo "==> Verifying integration"
uv run opensre integrations verify signoz

echo ""
echo "==> Query API smoke (logs, metrics, traces)"
uv run python - <<'PY'
from __future__ import annotations

import json
import os
import sys

from integrations.signoz import SigNozConfig
from integrations.signoz.client import SigNozClient

config = SigNozConfig(
    url=os.environ.get("SIGNOZ_URL", "http://localhost:8080"),
    api_key=os.environ["SIGNOZ_API_KEY"],
)
client = SigNozClient(config)

checks = [
    ("metrics", client.query_metrics(metric_name="signoz_calls_total", limit=5)),
    ("logs", client.query_logs(limit=5)),
    ("traces", client.query_traces(limit=5)),
    ("trace_summary", client.query_trace_summary()),
]

failed = False
for name, result in checks:
    print(f"\n--- {name} ---")
    print(json.dumps({k: v for k, v in result.items() if k not in ("logs", "metrics", "traces")}, indent=2))
    if name == "metrics":
        print(f"metrics rows: {len(result.get('metrics', []))}")
    elif name == "logs":
        print(f"log rows: {len(result.get('logs', []))}")
    elif name == "traces":
        print(f"trace rows: {len(result.get('traces', []))}")
    if not result.get("available"):
        print(f"FAIL: {result.get('error', 'unavailable')}", file=sys.stderr)
        failed = True

if failed:
    sys.exit(1)
print("\nAll Query API calls succeeded (empty result sets are OK if no telemetry yet).")
PY

echo ""
echo "Smoke test complete."
