#!/usr/bin/env bash
# demo/signoz/investigate.sh
# Trigger a synthetic SigNoz alert investigation via the OpenSRE CLI.

set -euo pipefail

cd "$(dirname "$0")/../.."

# Source env defaults if not already set
source demo/signoz/env.sh

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
