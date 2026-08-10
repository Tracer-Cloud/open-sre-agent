"""Kubernetes investigation guidance for gather stage."""

from __future__ import annotations


def kubernetes_gather_prompt_fragment() -> str:
    """Kubernetes investigation rules for gather stage."""
    return (
        "When the request names a cluster or environment, call "
        "kubernetes_list_clusters first and pass the matching name as cluster on "
        "every subsequent call; do not rely on the default cluster. "
        "When the request names a namespace, pass it as namespace. When it does "
        "not and the cluster is unfamiliar, call kubernetes_list_namespaces before "
        "concluding nothing is wrong. "
        "An empty pod/deployment list from one namespace is not evidence that a "
        "cluster is healthy or that a workload is absent — a workload may be an "
        "Argo Rollout; use kubernetes_list_workloads for 'does X exist / is it "
        "healthy'. Say which cluster and namespace were checked. "
        "A cloud project named in an alert is a monitoring scope, not a runtime "
        "location: monitors, alert policies, SLOs, logs and metrics are often "
        "created in a dedicated observability project while the services run "
        "elsewhere, and are just as often co-located — check, do not assume. "
        "Read the alert itself first: gcp_alerting on the project the alert "
        "names returns the open alert, its policy and the SLO, and the resource "
        "labels on an alert name the project the workload actually runs in. "
        "gcp_metrics_scope says which projects a scoping project monitors, and "
        "which scopes contain a workload project. When unsure which project "
        "holds a signal, sweep with project='*' where the tool accepts it, and "
        "otherwise query the observability project directly. If you still "
        "cannot place a workload, call kubernetes_search_fleet before saying it "
        "does not exist. An unavailable tool is a missing capability, never a "
        "missing connection or credential."
    )


__all__ = ["kubernetes_gather_prompt_fragment"]
