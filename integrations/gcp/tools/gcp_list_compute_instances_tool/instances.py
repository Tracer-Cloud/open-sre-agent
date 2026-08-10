"""Compute Engine instance normalization.

``aggregatedList`` returns a map keyed by scope (``zones/us-central1-a``) whose
values are *either* an ``instances`` list *or* a ``warning`` saying the scope
held nothing — reading only the former is correct, reading the map values as
lists is not. Flattening that here keeps the tool entrypoint about fan-out and
error handling.
"""

from __future__ import annotations

from typing import Any

#: Labels GKE stamps on the nodes it manages. Present on every node in a
#: cluster, which makes "this VM is a node of cluster X" a lookup rather than an
#: inference from the instance name.
_GKE_CLUSTER_LABEL = "goog-k8s-cluster-name"
_GKE_NODE_POOL_LABEL = "goog-k8s-node-pool-name"


def _basename(url: str) -> str:
    """Return the last path segment of a GCP self-link.

    Compute returns fully qualified URLs for ``zone`` and ``machineType``; the
    trailing segment is the only part anyone reads.
    """
    return str(url or "").rsplit("/", 1)[-1]


def _addresses(instance: dict[str, Any]) -> tuple[str, str]:
    """Return ``(internal ip, external ip)`` from the first network interface."""
    interfaces = instance.get("networkInterfaces")
    if not isinstance(interfaces, list) or not interfaces:
        return "", ""
    first = interfaces[0]
    if not isinstance(first, dict):
        return "", ""
    internal = str(first.get("networkIP", ""))
    external = ""
    configs = first.get("accessConfigs")
    if isinstance(configs, list):
        for config in configs:
            if isinstance(config, dict) and config.get("natIP"):
                external = str(config["natIP"])
                break
    return internal, external


def normalize_instance(instance: dict[str, Any], project: str) -> dict[str, Any]:
    """Flatten one Compute Engine instance into the shape the agent consumes."""
    internal, external = _addresses(instance)
    scheduling = instance.get("scheduling")
    scheduling = scheduling if isinstance(scheduling, dict) else {}
    labels = instance.get("labels")
    labels = labels if isinstance(labels, dict) else {}
    tags = instance.get("tags")
    tag_items = tags.get("items") if isinstance(tags, dict) else None

    normalized: dict[str, Any] = {
        "project": project,
        "name": str(instance.get("name", "")),
        "zone": _basename(instance.get("zone", "")),
        "machine_type": _basename(instance.get("machineType", "")),
        "status": str(instance.get("status", "")),
        "created": str(instance.get("creationTimestamp", "")),
        "internal_ip": internal,
    }
    if external:
        normalized["external_ip"] = external
    message = str(instance.get("statusMessage", "")).strip()
    if message:
        normalized["status_message"] = message
    if isinstance(tag_items, list) and tag_items:
        # Network tags decide which firewall rules apply, which is often the
        # answer when one VM in a pool cannot be reached.
        normalized["network_tags"] = [str(tag) for tag in tag_items]
    if labels:
        normalized["labels"] = {str(key): str(value) for key, value in labels.items()}
    cluster = labels.get(_GKE_CLUSTER_LABEL)
    if cluster:
        normalized["gke_cluster"] = str(cluster)
        pool = labels.get(_GKE_NODE_POOL_LABEL)
        if pool:
            normalized["gke_node_pool"] = str(pool)
    if scheduling.get("preemptible") or scheduling.get("provisioningModel") == "SPOT":
        # A preempted VM looks identical to a crashed one until you know it was
        # never guaranteed to keep running.
        normalized["preemptible"] = True
    platform = str(instance.get("cpuPlatform", "")).strip()
    if platform:
        normalized["cpu_platform"] = platform
    return normalized


def flatten_aggregated(items: Any, project: str) -> list[dict[str, Any]]:
    """Normalize every instance in an ``aggregatedList`` scope map."""
    if not isinstance(items, dict):
        return []
    instances: list[dict[str, Any]] = []
    for scope in items.values():
        if not isinstance(scope, dict):
            continue
        found = scope.get("instances")
        if not isinstance(found, list):
            continue
        instances.extend(
            normalize_instance(item, project) for item in found if isinstance(item, dict)
        )
    return instances
