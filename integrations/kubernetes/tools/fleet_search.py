"""Kubernetes fleet search tool for finding workloads across all clusters."""

from __future__ import annotations

import concurrent.futures
import contextvars
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from core.tool_framework.base import BaseTool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.kubernetes.tools import (
    _CLUSTER_PROP,
    _CLUSTER_SCOPED_PROPS,
    _UNAVAILABLE_MSG,
    _base_params,
    _is_available,
    _resolve_client,
)

#: One task per cluster, not per API call: the five kind calls inside a cluster
#: share one client and one urllib3 pool. core/execution.py already runs up to
#: _TOOL_EXECUTOR_WORKERS (10) tool calls in parallel, so a 17-wide fan-out from
#: inside one of them would be up to 170 threads. 8 caps the worst case at 80 and
#: clears 17 clusters in three waves. Deliberately not _MAX_PARALLEL_VERIFIERS (16).
_MAX_PARALLEL_CLUSTERS = 8

#: Absolute wall-clock budget for the whole fan-out. The gateway turn dies at
#: 240s; _BoundedApiClient allows 5s connect / 60s read PER REQUEST, so one wedged
#: cluster running five sequential kind calls can burn 300s on its own — longer
#: than the turn. 90s leaves room for the model's follow-up call in the same turn.
_FLEET_SEARCH_DEADLINE_SECONDS = 90.0


#: The only keys a match ever carries. The client's row projectors emit more
#: (labels, creation timestamps, container detail); everything outside this set
#: is dropped before the payload leaves the tool.
_MATCH_FIELDS = ("cluster", "namespace", "kind", "name", "ready", "desired", "phase")


def _submit_per_cluster(
    executor: ThreadPoolExecutor,
    search: Callable[..., dict[str, Any]],
    *args: Any,
) -> concurrent.futures.Future[dict[str, Any]]:
    """Submit one cluster's search on its own private copy of the caller's context.

    ``contextvars.Context`` is single-entry: ``Context.run`` sets an internal
    "entered" flag for the duration of the call, so a second *concurrent* ``run``
    on the same object raises ``RuntimeError: cannot enter context: <Context ...>
    is already entered``. Sharing one ``copy_context()`` across this fan-out
    therefore admits whichever worker wins the race and fails every other
    cluster — a 17-cluster fleet reports one searched and sixteen failed.

    The copy is taken **here**, on the submitting thread, not inside the worker:
    copying inside the worker would snapshot the pool thread's context and drop
    the caller's scope (``_CURRENT_SCOPE`` and friends), which is the whole
    reason the context is propagated at all.
    """
    return executor.submit(contextvars.copy_context().run, search, *args)


def _project_match(cluster_name: str, row: dict[str, Any]) -> dict[str, Any]:
    """Reduce one client row to the fixed, minimal match shape."""
    match = {field: row.get(field) for field in _MATCH_FIELDS}
    match["cluster"] = cluster_name
    return match


def _client_for(cluster_name: str, cluster_conn: dict[str, Any]) -> tuple[Any | None, str | None]:
    """Build the client for one cluster, returning ``(client, error)``.

    Delegates to the package's shared ``_resolve_client`` so the failure text is
    the one every other Kubernetes tool produces. Collapsing it into a generic
    "could not build a client" would destroy the reason an operator needs.
    """
    client, _conn, error = _resolve_client(cluster_name, {cluster_name: cluster_conn}, cluster_conn)
    if client is None:
        return None, error or _UNAVAILABLE_MSG
    return client, None


def _search_one_cluster_workloads(
    cluster_name: str,
    cluster_conn: dict[str, Any],
    name_contains: str,
    namespace: str,
) -> dict[str, Any]:
    """Search workload owners in one cluster (phase 1).

    Returns a dict with matches, success status, truncated kinds, and the kinds
    this cluster could not serve at all.
    """
    client, error = _client_for(cluster_name, cluster_conn)
    if client is None:
        return {
            "success": False,
            "error": error,
            "matches": [],
            "truncated_kinds": [],
            "unavailable_kinds": [],
        }

    result = client.search_workload_owners(name_contains, namespace)
    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error") or "unknown error",
            "matches": [],
            "truncated_kinds": [],
            "unavailable_kinds": [],
        }

    return {
        "success": True,
        "matches": [_project_match(cluster_name, row) for row in result.get("workloads", [])],
        "truncated_kinds": list(result.get("truncated_kinds", [])),
        "unavailable_kinds": [
            {"cluster": cluster_name, "kind": entry["kind"], "reason": entry["reason"]}
            for entry in result.get("unavailable_kinds", [])
        ],
    }


def _search_one_cluster_pods(
    cluster_name: str,
    cluster_conn: dict[str, Any],
    name_contains: str,
    namespace: str,
) -> dict[str, Any]:
    """Search individual pods in one cluster (phase 2)."""
    client, error = _client_for(cluster_name, cluster_conn)
    if client is None:
        return {"success": False, "error": error, "matches": [], "truncated_kinds": []}

    result = client.search_pods(name_contains, namespace)
    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error") or "unknown error",
            "matches": [],
            "truncated_kinds": [],
        }

    return {
        "success": True,
        "matches": [_project_match(cluster_name, row) for row in result.get("pods", [])],
        "truncated_kinds": ["Pod"] if result.get("truncated") else [],
    }


class KubernetesSearchFleetTool(BaseTool):
    """Find where a workload or pod runs across every registered cluster."""

    name = "kubernetes_search_fleet"
    source = "kubernetes"
    description = (
        "Find where a workload or pod runs across every registered cluster, when you "
        "do not already know the cluster. Matches a case-insensitive substring against "
        "Deployments, StatefulSets, DaemonSets, CronJobs and Argo Rollouts in all "
        "namespaces, and falls back to individual pods when no owner matches anywhere. "
        "Returns the cluster, namespace, kind and readiness of each match, plus the "
        "clusters it searched and any it could not reach — a result is only a reliable "
        "'does not exist' when clusters_failed is empty and partial is false."
    )
    use_cases = [
        "An alert names a workload but not a cluster",
        "An alert names a project that holds signals rather than the cluster the workload runs in",
        "Confirming a workload really is absent everywhere before saying so",
        "Finding which of several clusters runs a named service",
    ]
    anti_examples = [
        "You already know the cluster and namespace — use kubernetes_list_workloads, "
        "which is one call instead of a fan-out across every cluster.",
        "Listing everything in a namespace — this tool needs a name substring.",
        "Reading pod logs or restart counts — locate it here, then use "
        "kubernetes_list_pods or kubernetes_get_pod_logs on the cluster it returned.",
    ]
    surfaces = ("investigation", "chat", "action")
    requires = ["kubeconfig"]
    injected_params = [
        "kubeconfig",
        "kubeconfig_path",
        "context",
        "default_namespace",
        "cluster_configs",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "name_contains": {
                "type": "string",
                "description": (
                    "Case-insensitive substring of the workload or pod name to find. "
                    "Pass the name exactly as it appears in the alert — a full pod "
                    "name with its replicaset hash works, and so does a bare service "
                    "name."
                ),
            },
            "namespace": {
                "type": "string",
                "default": "",
                "description": (
                    "Optional namespace narrower. OMIT THIS to search every namespace, "
                    "which is the point of this tool. Pass one only when the request "
                    "names a namespace."
                ),
            },
            "cluster": _CLUSTER_PROP,
            "include_pods": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Search individual pods as well as their owners. Left false, pods "
                    "are searched automatically only when no owner matches anywhere."
                ),
            },
            # kubeconfig / kubeconfig_path / context are injected and pruned from
            # the model-facing schema; declare them for parity with the other tools.
            # Deliberately NOT ``_SHARED_KUBECONFIG_PROPS``: that bundle carries
            # ``_NAMESPACE_PROP``, whose text tells the model that omitting falls
            # back to the cluster's configured default. For fleet search that
            # guidance is exactly backwards.
            **{key: prop for key, prop in _CLUSTER_SCOPED_PROPS.items() if key != "cluster"},
        },
        "required": ["name_contains"],
    }
    outputs = {
        "matches": "Each match as {cluster, namespace, kind, name, ready, desired, phase}",
        "clusters_searched": "Cluster names searched end to end",
        "clusters_failed": "[{cluster, reason}] for every cluster that could not be searched",
        "partial": "True when a cluster failed or a list was truncated; 'not found' is unreliable",
        "truncated_kinds": "Kinds whose listing the server cut short, deduped across clusters",
        "unavailable_kinds": "[{cluster, kind, reason}] for kinds a cluster does not serve",
        "pods_searched": "Whether individual pods were searched as well as their owners",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        """Hide the tool unless a kubeconfig is actually configured.

        ``BaseTool`` defaults to always-available. Without this override the
        tool is advertised on the chat and action surfaces of a deployment with
        no Kubernetes at all, where every call can only return
        ``tool_unavailable``.
        """
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        """Supply the connection fields listed in ``injected_params``.

        ``core/execution.py`` builds the injected kwargs from this method alone.
        ``BaseTool`` returns ``{}``, so without this override ``kubeconfig``,
        ``context`` and ``cluster_configs`` never reach ``run`` — the tool falls
        through its own availability guard on every invocation and the fan-out
        has no clusters to search.
        """
        return _base_params(sources)

    def run(
        self,
        name_contains: str,
        namespace: str = "",
        cluster: str = "",
        include_pods: bool = False,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Search for workloads across the fleet."""
        if not _is_available(
            {"kubernetes": {"kubeconfig": kubeconfig, "kubeconfig_path": kubeconfig_path}}
        ):
            return tool_unavailable("kubernetes", _UNAVAILABLE_MSG)

        # Deliberately NOT passed through _effective_namespace. That helper
        # falls back to conn["namespace"], which _cluster_configs defaults to
        # "default" for every auto-registered cluster. Reusing it here would
        # search one usually-empty namespace per cluster, return zero across the
        # whole fleet, and be indistinguishable from a correct negative. An
        # empty namespace argument means every namespace, full stop.
        search_namespace = namespace.strip()

        # Use _resolve_client to handle cluster selection properly
        configs = cluster_configs or {}
        default_conn = {
            "kubeconfig": kubeconfig,
            "kubeconfig_path": kubeconfig_path,
            "context": context,
            "namespace": default_namespace,
        }

        # A named cluster is validated through the shared resolver so an unknown
        # name returns the error that lists the valid ones, rather than silently
        # falling back to the default cluster.
        selected_clusters: dict[str, dict[str, Any]]
        if cluster:
            _client, conn, error = _resolve_client(cluster, configs, default_conn)
            if error:
                return tool_unavailable("kubernetes", error)
            selected_clusters = {cluster: conn}
        elif configs:
            selected_clusters = dict(configs)
        else:
            selected_clusters = {"default": default_conn}

        # Phase 1: Search workload owners across fleet
        deadline = time.monotonic() + _FLEET_SEARCH_DEADLINE_SECONDS

        # Use ThreadPoolExecutor without context manager to control shutdown properly
        executor = ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CLUSTERS)

        try:
            # Submit phase 1 jobs
            phase1_futures = {
                _submit_per_cluster(
                    executor,
                    _search_one_cluster_workloads,
                    cname,
                    cconn,
                    name_contains,
                    search_namespace,
                ): cname
                for cname, cconn in selected_clusters.items()
            }

            # Wait for phase 1 with deadline
            phase1_done, phase1_pending = concurrent.futures.wait(
                phase1_futures, timeout=max(0.0, deadline - time.monotonic())
            )

            # Collect phase 1 results
            phase1_matches: list[dict[str, Any]] = []
            clusters_searched: list[str] = []
            clusters_failed: list[dict[str, str]] = []
            all_unavailable_kinds: list[dict[str, str]] = []
            truncated_kinds: set[str] = set()

            for future in phase1_done:
                cluster_name = phase1_futures[future]
                try:
                    result = future.result()
                    if result["success"]:
                        clusters_searched.append(cluster_name)
                        phase1_matches.extend(result["matches"])
                        all_unavailable_kinds.extend(result["unavailable_kinds"])
                        truncated_kinds.update(result["truncated_kinds"])
                    else:
                        clusters_failed.append({"cluster": cluster_name, "reason": result["error"]})
                except Exception as exc:
                    clusters_failed.append({"cluster": cluster_name, "reason": str(exc)})

            # Handle phase 1 timeouts
            for future in phase1_pending:
                cluster_name = phase1_futures[future]
                clusters_failed.append(
                    {"cluster": cluster_name, "reason": "search deadline exceeded"}
                )

            # Phase 2: Search pods only if fleet-wide phase 1 found nothing OR include_pods is True
            phase2_matches: list[dict[str, Any]] = []
            pods_searched = False

            if include_pods or len(phase1_matches) == 0:
                pods_searched = True

                # Only search clusters that succeeded in phase 1
                phase2_futures = {
                    _submit_per_cluster(
                        executor,
                        _search_one_cluster_pods,
                        cname,
                        selected_clusters[cname],
                        name_contains,
                        search_namespace,
                    ): cname
                    for cname in clusters_searched
                }

                # Wait for phase 2 with remaining deadline
                phase2_done, phase2_pending = concurrent.futures.wait(
                    phase2_futures, timeout=max(0.0, deadline - time.monotonic())
                )

                for future in phase2_done:
                    cluster_name = phase2_futures[future]
                    try:
                        result = future.result()
                        if result["success"]:
                            phase2_matches.extend(result["matches"])
                            truncated_kinds.update(result["truncated_kinds"])
                        else:
                            # Phase 2 failure: move cluster from searched to failed
                            clusters_searched.remove(cluster_name)
                            clusters_failed.append(
                                {"cluster": cluster_name, "reason": result["error"]}
                            )
                    except Exception as exc:
                        clusters_searched.remove(cluster_name)
                        clusters_failed.append({"cluster": cluster_name, "reason": str(exc)})

                # Handle phase 2 timeouts
                for future in phase2_pending:
                    cluster_name = phase2_futures[future]
                    clusters_searched.remove(cluster_name)
                    clusters_failed.append(
                        {"cluster": cluster_name, "reason": "search deadline exceeded"}
                    )

        finally:
            # Properly shutdown executor without context manager re-joining
            executor.shutdown(wait=False, cancel_futures=True)

        # Combine all matches
        all_matches = phase1_matches + phase2_matches

        # Sorted for determinism: futures complete in an arbitrary order, and an
        # unsorted payload produces divergent test ids under xdist.
        all_matches.sort(
            key=lambda match: (
                match["cluster"] or "",
                match["namespace"] or "",
                match["kind"] or "",
                match["name"] or "",
            )
        )
        clusters_failed.sort(key=lambda entry: entry["cluster"])
        all_unavailable_kinds.sort(key=lambda entry: (entry["cluster"], entry["kind"]))

        # Truncation comes from the server's continue token (``_list_was_truncated``),
        # never from ``len(rows) == limit``: the server truncates arbitrarily, so a
        # truncated list may genuinely hide the match.
        truncated = bool(truncated_kinds)

        # A cluster the search could not finish, or a list the server cut short,
        # both mean "not found" is not yet an answer. An absent CRD does not:
        # "there are no Rollouts here" is a complete answer.
        partial = bool(clusters_failed) or truncated

        return {
            "source": "kubernetes",
            "available": True,
            "query": name_contains,
            "matches": all_matches,
            "total": len(all_matches),
            "clusters_searched": sorted(clusters_searched),
            "clusters_failed": clusters_failed,
            "pods_searched": pods_searched,
            "partial": partial,
            "truncated": truncated,
            "truncated_kinds": sorted(truncated_kinds),
            "unavailable_kinds": all_unavailable_kinds,
        }


# Export the tool instance
tool = KubernetesSearchFleetTool()
