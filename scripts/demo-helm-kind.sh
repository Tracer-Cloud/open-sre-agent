#!/usr/bin/env bash
# Local Helm + kind demo helper (e.g. issue #321 maintainer demo).
# Run from repo root: ./scripts/demo-helm-kind.sh
# Requires: Docker (running), kind, kubectl, helm.
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-opensre-helm-demo}"
NAMESPACE="${NAMESPACE:-demo}"
RELEASE="${RELEASE:-demo}"

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' not found — install it and retry." >&2
    exit 1
  }
}

require docker
require kind
require kubectl
require helm

# `docker info` (and sometimes even `docker ps`) can hang for a long time if the
# daemon is wedged or the CLI context points at a dead socket — avoid infinite wait.
docker_daemon_ok() {
  require python3
  local wait_secs="${DOCKER_WAIT_SECONDS:-25}"
  python3 -c "
import subprocess, sys
wait = int(sys.argv[1])
try:
    subprocess.run(
        ['docker', 'info'],
        check=True,
        timeout=wait,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
except subprocess.TimeoutExpired:
    print('error: Docker did not respond within %ss (timed out).' % wait, file=sys.stderr)
    print('  Fix: quit & restart Docker Desktop, or run: docker context ls', file=sys.stderr)
    sys.exit(124)
except (subprocess.CalledProcessError, FileNotFoundError) as exc:
    print('error: Docker CLI failed:', exc, file=sys.stderr)
    sys.exit(1)
" "${wait_secs}"
}

if ! docker_daemon_ok; then
  echo "  Also try in a fresh terminal: docker info" >&2
  echo "  If that hangs too, the daemon is not healthy even if the menu bar icon looks 'running'." >&2
  exit 1
fi

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "kind cluster '${CLUSTER_NAME}' already exists — reusing it."
else
  echo "Creating kind cluster '${CLUSTER_NAME}' …"
  kind create cluster --name "${CLUSTER_NAME}"
fi

echo "Setting kubectl context to kind-${CLUSTER_NAME} …"
kubectl config use-context "kind-${CLUSTER_NAME}"
kubectl cluster-info
kubectl get nodes

echo "Adding Helm repo and installing sample release …"
helm repo add bitnami https://charts.bitnami.com/bitnami --force-update
helm repo update

if helm status "${RELEASE}" --namespace "${NAMESPACE}" >/dev/null 2>&1; then
  echo "Helm release '${RELEASE}' already installed in '${NAMESPACE}' — skipping install."
else
  helm install "${RELEASE}" bitnami/nginx --namespace "${NAMESPACE}" --create-namespace --wait
fi

echo ""
echo "=== Helm / K8s snapshot (use this for screen recording) ==="
helm list -n "${NAMESPACE}"
helm status "${RELEASE}" -n "${NAMESPACE}"
helm history "${RELEASE}" -n "${NAMESPACE}"
kubectl get pods,svc -n "${NAMESPACE}"
echo ""
echo "Optional Helm introspection:"
echo "  helm get values ${RELEASE} -n ${NAMESPACE}"
echo "  helm get manifest ${RELEASE} -n ${NAMESPACE} | head -80"
echo ""
echo "Teardown when finished:"
echo "  kind delete cluster --name ${CLUSTER_NAME}"
