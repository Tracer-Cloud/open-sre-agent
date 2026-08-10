"""Join discovered GKE clusters to registered Kubernetes instances.

The Kubernetes tools address a cluster by *registered instance name* — the
``cluster`` argument resolved against ``cluster_configs`` in
``integrations/kubernetes/tools``. The GCP API knows nothing about that
registry, so a bare listing tells the agent a cluster exists without telling it
whether ``kubernetes_list_pods`` can reach it. That gap is the whole reason this
module exists: discovery is only useful if it says what to call next.

Matching is deliberately exact — two rules, both unambiguous:

1. the instance's kubeconfig ``context`` equals the context gcloud writes for
   that cluster (``gke_<project>_<location>_<name>``), or
2. the instance's registered name equals the cluster name.

Substring or prefix matching was considered and rejected: two clusters called
``prod`` in different projects are ordinary, and a wrong match would send the
agent to the wrong cluster with no signal that it had happened.
"""

from __future__ import annotations

from typing import Any


def _index(registered: list[dict[str, Any]] | None) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(context -> instance, name -> instance)`` lookups, lowercased."""
    by_context: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for entry in registered or []:
        if not isinstance(entry, dict):
            continue
        instance = str(entry.get("name", "")).strip()
        if not instance:
            continue
        by_name.setdefault(instance.lower(), instance)
        context = str(entry.get("context", "")).strip()
        if context:
            by_context.setdefault(context.lower(), instance)
    return by_context, by_name


def annotate(
    clusters: list[dict[str, Any]],
    registered: list[dict[str, Any]] | None,
) -> list[str]:
    """Stamp ``registered_as`` onto each match; return the unmatched names.

    Mutates ``clusters`` in place — they are freshly normalized dicts owned by
    the caller, so there is nothing to copy defensively.
    """
    by_context, by_name = _index(registered)
    unmatched: list[str] = []
    for cluster in clusters:
        context = str(cluster.get("kubeconfig_context", "")).lower()
        name = str(cluster.get("name", "")).lower()
        instance = by_context.get(context) or by_name.get(name)
        if instance:
            cluster["registered_as"] = instance
        else:
            unmatched.append(str(cluster.get("name", "")))
    return unmatched
