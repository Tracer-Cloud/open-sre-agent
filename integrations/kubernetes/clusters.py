"""Register and manage multiple named Kubernetes clusters.

The interactive ``setup kubernetes`` flow configures ONE cluster and mirrors it
to ``.env`` (a single-slot file). This module registers *additional* named
clusters — for example one GKE cluster per GCP project — as store-only
instances that the tools target by name via the ``cluster`` argument. It never
writes ``.env`` and never clobbers sibling instances: each named cluster is
appended to (or updated within) the kubernetes record's ``instances`` list via
:func:`integrations.store.upsert_instance`.

Domain logic only — the CLI surface (``opensre integrations add-cluster`` and
friends) is a thin adapter over these functions.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations import store
from integrations.kubernetes.verifier import verify_kubernetes

_SERVICE = "kubernetes"


@dataclass(frozen=True)
class ClusterResult:
    """Outcome of a cluster mutation, in a shape any surface can render."""

    ok: bool
    detail: str


@dataclass(frozen=True)
class ClusterSummary:
    """One registered cluster, for listing."""

    name: str
    tags: dict[str, str]
    context: str
    namespace: str


def list_clusters() -> list[ClusterSummary]:
    """Return every registered kubernetes cluster, default (first) instance first."""
    summaries: list[ClusterSummary] = []
    for instance in store.get_instances(_SERVICE):
        credentials = instance.get("credentials", {}) or {}
        summaries.append(
            ClusterSummary(
                name=str(instance.get("name", "default")),
                tags={str(k): str(v) for k, v in (instance.get("tags", {}) or {}).items()},
                context=str(credentials.get("context", "")),
                namespace=str(credentials.get("namespace", "default")),
            )
        )
    return summaries


def add_cluster(
    *,
    name: str,
    kubeconfig_path: str = "",
    kubeconfig: str = "",
    context: str = "",
    namespace: str = "default",
    tags: dict[str, str] | None = None,
    verify: bool = True,
) -> ClusterResult:
    """Register (or update) a named cluster after verifying connectivity.

    Exactly one of ``kubeconfig_path`` or ``kubeconfig`` (inline YAML) must be
    given. When ``verify`` is true, the cluster is probed before it is stored,
    so a bad kubeconfig or unreachable API never leaves a broken instance
    behind. The instance is keyed by ``name`` (case-insensitive) within the
    kubernetes record, so re-running with the same name updates it in place.
    """
    name = (name or "").strip()
    if not name:
        return ClusterResult(ok=False, detail="Cluster name is required.")
    if not kubeconfig_path and not kubeconfig:
        return ClusterResult(
            ok=False, detail="Provide a kubeconfig path or inline kubeconfig YAML."
        )
    if kubeconfig_path and kubeconfig:
        return ClusterResult(
            ok=False, detail="Provide only one of kubeconfig path or inline kubeconfig YAML."
        )

    credentials = {
        "kubeconfig_path": kubeconfig_path,
        "kubeconfig": kubeconfig,
        "context": context,
        "namespace": namespace or "default",
    }
    if verify:
        probe = {key: value for key, value in credentials.items() if value}
        outcome = verify_kubernetes("add-cluster", probe)
        if outcome["status"] != "passed":
            return ClusterResult(ok=False, detail=outcome["detail"])

    store.upsert_instance(
        _SERVICE,
        {
            "name": name,
            "tags": tags or {},
            # Drop empty fields so the stored instance stays minimal; the
            # classifier fills missing keys with the same defaults used here.
            "credentials": {key: value for key, value in credentials.items() if value},
        },
    )
    return ClusterResult(ok=True, detail=f"Cluster '{name.lower()}' registered.")


def remove_cluster(name: str) -> ClusterResult:
    """Remove a named cluster from the store. No-op-safe for unknown names."""
    name = (name or "").strip()
    if not name:
        return ClusterResult(ok=False, detail="Cluster name is required.")
    if store.remove_instance(_SERVICE, name):
        return ClusterResult(ok=True, detail=f"Cluster '{name.lower()}' removed.")
    return ClusterResult(ok=False, detail=f"No cluster named '{name.lower()}' is registered.")


__all__ = [
    "ClusterResult",
    "ClusterSummary",
    "add_cluster",
    "list_clusters",
    "remove_cluster",
]
