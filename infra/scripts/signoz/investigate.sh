#!/usr/bin/env bash
# infra/scripts/signoz/investigate.sh
# Trigger a synthetic SigNoz alert investigation via the OpenSRE CLI.
# Run from repo root: bash infra/scripts/signoz/investigate.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# Source env defaults if not already set
source "$SCRIPT_DIR/env.sh"

ALERT_PAYLOAD='{
  "alert_source": "signoz",
  "alert_name": "HighErrorRate",
  "pipeline_name": "payment-service",
  "severity": "critical",
  "commonLabels": {
    "alertname": "HighErrorRate",
    "service_name": "payment-service",
    "severity": "critical"
  },
  "commonAnnotations": {
    "summary": "Error rate exceeded 5% for payment-service"
  },
  "startsAt": "2024-01-15T10:00:00Z"
}'

echo "Running SigNoz investigation ..."
uv run opensre investigate --input-json "$ALERT_PAYLOAD"
