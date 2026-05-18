#!/usr/bin/env bash
# demo/signoz/verify.sh
# Run OpenSRE verification against the local SigNoz stack.

set -euo pipefail

cd "$(dirname "$0")/../.."

# Source env defaults if not already set
source demo/signoz/env.sh

echo "Verifying SigNoz integration ..."
uv run opensre integrations verify signoz

echo ""
echo "Verification complete."
