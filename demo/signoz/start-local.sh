#!/usr/bin/env bash
# demo/signoz/start-local.sh
# Convenience wrapper around the official SigNoz Docker Compose setup.
#
# Official docs:
#   https://signoz.io/docs/install/docker/#install-signoz-using-docker-compose
#
# This script intentionally mirrors those steps:
#   git clone -b main https://github.com/SigNoz/signoz.git
#   cd signoz/deploy/docker
#   docker compose up -d --remove-orphans

set -euo pipefail

SIGNOZ_DIR="${SIGNOZ_DIR:-$HOME/signoz}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "docker daemon is not running"
  exit 1
fi

if [[ ! -d "$SIGNOZ_DIR/.git" ]]; then
  echo "Cloning SigNoz into: $SIGNOZ_DIR"
  git clone -b main https://github.com/SigNoz/signoz.git "$SIGNOZ_DIR"
else
  echo "Using existing SigNoz checkout at: $SIGNOZ_DIR"
fi

cd "$SIGNOZ_DIR/deploy/docker"
docker compose up -d --remove-orphans

echo ""
echo "SigNoz is starting."
echo "UI: http://localhost:8080"
echo "Collector OTLP gRPC: localhost:4317"
echo "Collector OTLP HTTP: localhost:4318"
echo ""
echo "To verify containers:"
echo "  docker ps"
