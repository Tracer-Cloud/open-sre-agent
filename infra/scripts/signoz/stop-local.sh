#!/usr/bin/env bash
# infra/scripts/signoz/stop-local.sh
# Convenience wrapper around official SigNoz docker compose teardown.

set -euo pipefail

SIGNOZ_DIR="${SIGNOZ_DIR:-$HOME/signoz}"
COMPOSE_DIR="$SIGNOZ_DIR/deploy/docker"

if [[ ! -d "$COMPOSE_DIR" ]]; then
  echo "SigNoz compose directory not found: $COMPOSE_DIR"
  echo "Set SIGNOZ_DIR to your SigNoz checkout path and retry."
  exit 1
fi

cd "$COMPOSE_DIR"
docker compose down
echo "SigNoz stopped."
