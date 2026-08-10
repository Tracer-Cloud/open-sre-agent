"""``extract_params`` for the GKE listing tool.

The standard GCP payload plus the *names* of registered Kubernetes instances —
enough to tell the agent which discovered cluster it can already run kubectl
against, and nothing more. Deliberately no kubeconfig contents: this tool never
connects to a cluster, so carrying the credential through the tool-call
boundary would widen the blast radius for no benefit.
"""

from __future__ import annotations

from typing import Any

from integrations.gcp.tool_params import gcp_tool_params
from integrations.selectors import get_instances


def registered_clusters(sources: dict[str, Any]) -> list[dict[str, str]]:
    """Return ``[{"name", "context"}]`` for every registered Kubernetes instance."""
    entries: list[dict[str, str]] = []
    for instance in get_instances(sources, "kubernetes"):
        name = str(instance.get("name", "")).strip()
        if not name:
            continue
        config = instance.get("config") or {}
        entries.append({"name": name, "context": str(config.get("context", "") or "").strip()})
    return entries


def gke_tool_params(sources: dict[str, dict]) -> dict[str, Any]:
    """GCP project scope plus the Kubernetes instances the agent can address."""
    return {**gcp_tool_params(sources), "registered_clusters": registered_clusters(sources)}
