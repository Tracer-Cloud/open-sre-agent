#!/usr/bin/env bash
# infra/scripts/signoz/env.sh
# Source this file to export SigNoz Query API env vars for OpenSRE.

export SIGNOZ_URL="${SIGNOZ_URL:-http://localhost:8080}"
export SIGNOZ_API_KEY="${SIGNOZ_API_KEY:-}"

echo "SigNoz environment configured:"
echo "  SIGNOZ_URL=$SIGNOZ_URL"
echo "  SIGNOZ_API_KEY=${SIGNOZ_API_KEY:+***set***}"
