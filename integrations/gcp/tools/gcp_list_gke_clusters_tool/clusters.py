"""GKE ``Cluster`` normalization.

A ``container/v1`` cluster resource carries roughly sixty fields, most of them
provisioning detail that answers no incident question. This keeps the ones that
do: whether the control plane is healthy, what version it runs, how many nodes
back it, and which node pool is the unhealthy one.

Kept separate from the tool entrypoint so shape handling is testable without an
API client.
"""

from __future__ import annotations

from typing import Any

#: A cluster is only unambiguously fine in this state. Everything else —
#: PROVISIONING, RECONCILING, STOPPING, DEGRADED, ERROR — is worth surfacing
#: during an investigation, so ``healthy`` is a whitelist, not a blacklist.
RUNNING = "RUNNING"


def kubeconfig_context(project: str, location: str, name: str) -> str:
    """Return the context name ``gcloud container clusters get-credentials`` writes.

    Deterministic and documented by gcloud, which makes it a reliable join key
    between a cluster discovered from the GCP API and a kubeconfig someone
    generated earlier — see
    :mod:`integrations.gcp.tools.gcp_list_gke_clusters_tool.correlation`.
    """
    return f"gke_{project}_{location}_{name}"


def _sub_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``parent[key]`` when it is an object, otherwise an empty one."""
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _node_pool(pool: dict[str, Any]) -> dict[str, Any]:
    config = _sub_object(pool, "config")
    autoscaling = _sub_object(pool, "autoscaling")
    normalized: dict[str, Any] = {
        "name": str(pool.get("name", "")),
        "status": str(pool.get("status", "")),
        "node_count": pool.get("initialNodeCount", 0),
        "version": str(pool.get("version", "")),
        "machine_type": str(config.get("machineType", "")),
    }
    if autoscaling.get("enabled"):
        low = autoscaling.get("minNodeCount", 0)
        high = autoscaling.get("maxNodeCount", 0)
        normalized["autoscaling"] = f"{low}-{high}"
    message = str(pool.get("statusMessage", "")).strip()
    if message:
        normalized["status_message"] = message
    return normalized


def _conditions(cluster: dict[str, Any]) -> list[str]:
    """Render cluster conditions as plain lines.

    Conditions are where GKE reports the actual fault — quota exhaustion, an
    unreachable master, a stuck upgrade — while ``status`` says only DEGRADED.
    """
    raw = cluster.get("conditions")
    if not isinstance(raw, list):
        return []
    lines: list[str] = []
    for condition in raw:
        if not isinstance(condition, dict):
            continue
        code = str(condition.get("canonicalCode") or condition.get("code") or "").strip()
        message = str(condition.get("message", "")).strip()
        rendered = f"{code}: {message}" if code and message else message or code
        if rendered:
            lines.append(rendered)
    return lines


def normalize_cluster(cluster: dict[str, Any], project: str) -> dict[str, Any]:
    """Flatten one GKE cluster into the compact shape the agent consumes."""
    # ``location`` is the modern field; ``zone`` is its deprecated predecessor
    # and is still populated for zonal clusters created years ago.
    location = str(cluster.get("location") or cluster.get("zone") or "")
    name = str(cluster.get("name", ""))
    status = str(cluster.get("status", ""))
    conditions = _conditions(cluster)

    autopilot = _sub_object(cluster, "autopilot")
    channel = _sub_object(cluster, "releaseChannel")
    private = _sub_object(cluster, "privateClusterConfig")
    labels = cluster.get("resourceLabels")
    pools = cluster.get("nodePools")

    normalized: dict[str, Any] = {
        "project": project,
        "name": name,
        "location": location,
        "status": status,
        "healthy": status == RUNNING and not conditions,
        "master_version": str(cluster.get("currentMasterVersion", "")),
        "node_version": str(cluster.get("currentNodeVersion", "")),
        "node_count": cluster.get("currentNodeCount", 0),
        "autopilot": bool(autopilot.get("enabled")),
        "kubeconfig_context": kubeconfig_context(project, location, name),
    }

    release = str(channel.get("channel", "")).strip()
    if release:
        normalized["release_channel"] = release
    if private.get("enablePrivateNodes"):
        # Worth flagging: a private cluster is unreachable from outside the VPC,
        # which explains a kubectl timeout that otherwise looks like an outage.
        normalized["private_nodes"] = True
        normalized["private_endpoint_only"] = bool(private.get("enablePrivateEndpoint"))
    message = str(cluster.get("statusMessage", "")).strip()
    if message:
        normalized["status_message"] = message
    if conditions:
        normalized["conditions"] = conditions
    if isinstance(labels, dict) and labels:
        normalized["labels"] = {str(key): str(value) for key, value in labels.items()}
    if isinstance(pools, list):
        normalized["node_pools"] = [_node_pool(pool) for pool in pools if isinstance(pool, dict)]
    return normalized


def normalize_clusters(clusters: list[Any], project: str) -> list[dict[str, Any]]:
    """Normalize a listing, skipping anything that is not an object."""
    return [normalize_cluster(item, project) for item in clusters if isinstance(item, dict)]
