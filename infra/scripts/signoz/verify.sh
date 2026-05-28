#!/usr/bin/env bash
# infra/scripts/signoz/verify.sh
# Run OpenSRE verification against the local SigNoz stack.
# Run from repo root: bash infra/scripts/signoz/verify.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# Source env defaults if not already set
source "$SCRIPT_DIR/env.sh"

echo "Verifying SigNoz integration ..."
uv run opensre integrations verify signoz

echo ""
echo "Verification complete."
