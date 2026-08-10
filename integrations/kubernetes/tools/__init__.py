"""Kubernetes investigation tools — Kubernetes Python SDK backed.

Multi-cluster: each registered kubernetes instance is one cluster (its own
kubeconfig/context/namespace — e.g. one GKE cluster in one GCP project). Every
tool takes an optional ``cluster`` argument naming which registered instance to
target; omitting it uses the default (first) instance, preserving
single-cluster behavior. Discover the registered names with
``kubernetes_list_clusters``.
"""

from __future__ import annotations

from typing import Any

from config.constants import (
    DEFAULT_KUBERNETES_NAMESPACE,
    KUBERNETES_MAX_LOG_TAIL_LINES,
)
from core.tool_framework.base import BaseTool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations import selectors
from integrations.config_models import KubernetesIntegrationConfig
from integrations.kubernetes.client import _RESOURCE_DISPATCH, KubernetesClient

_RESOURCE_TYPE_ENUM: list[str] = sorted(_RESOURCE_DISPATCH.keys())

_UNAVAILABLE_MSG = "Kubernetes integration is not configured (missing kubeconfig)."

#: A registered fleet of one cannot have an unchecked cluster; below this the
#: caveat is noise with no lever behind it.
_MIN_CLUSTERS_FOR_SCOPE_NOTE = 2

#: Deployment, StatefulSet, DaemonSet, CronJob, Rollout — the kinds
#: ``list_workloads`` probes. Most clusters in this fleet do not run Argo
#: Rollouts, so a 404 on that one kind alone is the common case, not evidence
#: the namespace was unreadable. Only suppress the scope note when EVERY kind
#: was unreadable — one missing CRD must not silently turn the note off on a
#: cluster where the other four kinds read cleanly and empty.
_WORKLOAD_KIND_COUNT = 5

#: Emitted on a zero-result workload lookup that ran against the default
#: cluster while other clusters are registered. Live incident: an investigation
#: read "zero deployments in default/identity/one-platform" off 1 of 17
#: registered clusters and reported the credential-deleting workload absent and
#: the blast radius clear. The prompt already forbade that (gather_prompt.py)
#: and the model did it anyway, so the correction has to be in the observation.
_FLEET_SCOPE_NOTE = (
    "Scope caveat: no cluster argument was passed, so this checked 1 of {total} "
    "registered clusters — the default one. An empty result here is NOT evidence "
    "that the workload is absent from the fleet or that the blast radius is clear. "
    "To settle that, call kubernetes_search_fleet with name_contains set to the "
    "workload name: it matches Deployments, StatefulSets, DaemonSets, CronJobs, "
    "Rollouts and pods in every namespace of all {total} clusters, and reports any "
    "it could not reach. If you have no name to search for, call "
    "kubernetes_list_clusters and re-run this call with cluster set to each name."
)

#: Appended to a 404 on a by-name lookup, mirroring the get_pod_logs 404 hint in
#: integrations/kubernetes/client.py.
_FLEET_SCOPE_NOT_FOUND_HINT = (
    " — this lookup ran against the default cluster only, 1 of {total} registered; "
    "use kubernetes_search_fleet with name_contains set to that name to locate it "
    "across all {total} clusters instead of guessing another name here"
)


def _make_client(sources: dict[str, Any]) -> KubernetesClient | None:
    k8s = sources.get("kubernetes", {})
    kubeconfig = k8s.get("kubeconfig", "")
    kubeconfig_path = k8s.get("kubeconfig_path", "")
    if not kubeconfig and not kubeconfig_path:
        return None
    try:
        cfg = KubernetesIntegrationConfig.model_validate(
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": k8s.get("context", ""),
                "namespace": k8s.get("namespace", "default"),
            }
        )
        return KubernetesClient(cfg)
    except Exception:
        return None


def _is_available(sources: dict[str, Any]) -> bool:
    k8s = sources.get("kubernetes", {})
    return bool(k8s.get("kubeconfig") or k8s.get("kubeconfig_path"))


def _cluster_configs(sources: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map each registered kubernetes instance name to its connection fields.

    Built from ``selectors.get_instances`` so the map covers every registered
    cluster, not just the default instance. A single-instance setup yields one
    ``{"default": {...}}`` entry.
    """
    configs: dict[str, dict[str, Any]] = {}
    for instance in selectors.get_instances(sources, "kubernetes"):
        name = str(instance.get("name", "")).strip()
        if not name:
            continue
        config = instance.get("config", {}) or {}
        configs[name] = {
            "kubeconfig": config.get("kubeconfig", ""),
            "kubeconfig_path": config.get("kubeconfig_path", ""),
            "context": config.get("context", ""),
            "namespace": config.get("namespace", "default"),
        }
    return configs


def _clusters_list(sources: dict[str, Any]) -> list[dict[str, Any]]:
    """Registered clusters as ``[{name, tags, is_default}]`` for discovery."""
    clusters: list[dict[str, Any]] = []
    for index, instance in enumerate(selectors.get_instances(sources, "kubernetes")):
        name = str(instance.get("name", "")).strip()
        if not name:
            continue
        tags = instance.get("tags", {})
        clusters.append(
            {
                "name": name,
                "tags": dict(tags) if isinstance(tags, dict) else {},
                "is_default": index == 0,
            }
        )
    return clusters


def _base_params(sources: dict[str, Any]) -> dict[str, Any]:
    """Injected default connection fields plus the map of every registered cluster.

    ``cluster_configs`` is trusted connection configuration, so every tool lists
    it in ``injected_params``: the runtime re-forces the extracted value over
    anything the model sends, exactly as it protects ``kubeconfig`` and
    ``context``. ``run`` uses it to resolve an LLM-chosen ``cluster`` name to
    that instance's connection fields — the model picks the *name*, never the
    connection map.

    The connection default travels as ``default_namespace`` rather than
    ``namespace`` because the runtime merges ``{**injected, **tc.input}`` — an
    injected key named ``namespace`` would occupy the model's parameter slot
    whether or not it is protected. The connection default therefore travels
    under a distinct protected name and is applied by ``_effective_namespace``
    only when the model supplied nothing.
    """
    k8s = sources.get("kubernetes", {})
    return {
        "kubeconfig": k8s.get("kubeconfig", ""),
        "kubeconfig_path": k8s.get("kubeconfig_path", ""),
        "context": k8s.get("context", ""),
        "default_namespace": k8s.get("namespace", "") or DEFAULT_KUBERNETES_NAMESPACE,
        "cluster_configs": _cluster_configs(sources),
    }


def _resolve_client(
    cluster: str,
    cluster_configs: dict[str, Any] | None,
    default_conn: dict[str, Any],
) -> tuple[KubernetesClient | None, dict[str, Any], str | None]:
    """Pick the target cluster, then build a client for it.

    Returns ``(client, connection_fields, error)``. ``error`` is ``None`` on
    success. An empty ``cluster`` uses ``default_conn`` (the injected default
    instance), preserving single-cluster behavior. A named ``cluster`` is
    looked up in ``cluster_configs``; an unknown name returns an error listing
    the valid names rather than silently falling back.
    """
    configs = cluster_configs or {}
    if cluster:
        conn = configs.get(cluster)
        if conn is None:
            available = sorted(configs)
            return None, {}, f"unknown cluster '{cluster}'; available clusters: {available}"
    else:
        conn = default_conn
    client = _make_client({"kubernetes": conn})
    if client is None:
        return None, conn, "Kubernetes integration is not configured (missing kubeconfig)."
    return client, conn, None


def _effective_namespace(requested: str, conn: dict[str, Any]) -> str:
    """Resolve the effective namespace for a request.

    Precedence, highest first:

    1. ``requested`` — the model-supplied ``namespace`` argument.
    2. ``conn["namespace"]`` — the *resolved* cluster's configured namespace.
       ``conn`` is whichever instance ``_resolve_client`` selected, so naming a
       ``cluster`` already switches this to that cluster's own default; the
       injected default instance's namespace cannot leak across clusters.
    3. ``DEFAULT_KUBERNETES_NAMESPACE``.

    The model argument must win at step 1 even when a ``cluster`` was named:
    "pods in payments on the prod cluster" carries both selectors, and the
    prompt guidance tells the model to send both.
    """
    if requested.strip():
        return requested.strip()
    return conn.get("namespace") or DEFAULT_KUBERNETES_NAMESPACE


def _fleet_scope_note(
    template: str,
    cluster: str,
    cluster_configs: dict[str, Any] | None,
) -> str:
    """Return the unchecked-fleet caveat, or "" when it would be noise.

    Fires only when the call used the default cluster (no ``cluster``
    argument, the same truthiness test ``_resolve_client`` applies) AND more
    than one cluster is registered. ``len(cluster_configs)`` is deliberately
    unfiltered: it is the exact set ``kubernetes_search_fleet`` fans out over
    (fleet_search.py builds ``selected_clusters = dict(configs)``), so the
    number quoted here is the number that tool will account for.
    """
    total = len(cluster_configs or {})
    if cluster or total < _MIN_CLUSTERS_FOR_SCOPE_NOTE:
        return ""
    return template.format(total=total)


_CLUSTER_PROP: dict[str, Any] = {
    "type": "string",
    "default": "",
    "description": (
        "Registered Kubernetes cluster/instance to target. Use kubernetes_list_clusters "
        "to see valid names. Pass this whenever the request names or implies a specific "
        "cluster or environment. Omitting uses the default cluster only, which may not "
        "be the one asked about — a resulting empty or not-found result does not mean "
        "the workload is absent from the fleet."
    ),
}

_NAMESPACE_PROP: dict[str, Any] = {
    "type": "string",
    "default": "",
    "description": (
        "Kubernetes namespace to target. Pass the namespace the user named, or one "
        "returned by kubernetes_list_namespaces. Omit ONLY when no namespace is "
        "indicated — omitting falls back to the cluster's configured default "
        "namespace, which is usually 'default' and usually holds no workloads."
    ),
}

_CLUSTER_SCOPED_PROPS: dict[str, Any] = {
    "kubeconfig": {"type": "string", "description": "Raw kubeconfig YAML string"},
    "kubeconfig_path": {
        "type": "string",
        "default": "",
        "description": "Path to kubeconfig file (alternative to kubeconfig)",
    },
    "context": {"type": "string", "default": "", "description": "Kubeconfig context to use"},
    "cluster": _CLUSTER_PROP,
}

_SHARED_KUBECONFIG_PROPS: dict[str, Any] = {
    **_CLUSTER_SCOPED_PROPS,
    "namespace": _NAMESPACE_PROP,
}


class KubernetesListPodsTool(BaseTool):
    """List pods in a Kubernetes namespace to diagnose availability and restart issues."""

    name = "kubernetes_list_pods"
    source = "kubernetes"
    description = (
        "List pods in a Kubernetes namespace. Returns pod phase, container readiness, "
        "restart counts, and node assignment. Use to detect crash-looping or pending pods."
    )
    use_cases = [
        "Checking if pods are in a crash-loop or pending state",
        "Identifying which pods are not ready or have high restart counts",
        "Filtering pods by label selector to scope investigation",
        "Verifying that a deployment's pods are running after a rollout",
    ]
    anti_examples = [
        "Locating a pod when you do not know which cluster or namespace runs it (use kubernetes_search_fleet)",
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
            **_SHARED_KUBECONFIG_PROPS,
            "label_selector": {
                "type": "string",
                "default": "",
                "description": "Label selector filter (e.g. 'app=my-service')",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of pods to return",
            },
        },
        "required": [],
    }
    outputs = {
        "pods": "List of pods with phase, container statuses, and node assignment",
        "total": "Total number of pods returned",
        "note": "Present only when an empty result may be a single-cluster false negative",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        label_selector: str = "",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, pods=[], total=0)
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.list_pods(
                namespace=namespace, label_selector=label_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), pods=[], total=0
                )
            payload: dict[str, Any] = {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "pods": result["pods"],
                "total": result["total"],
            }
            if result["total"] == 0:
                note = _fleet_scope_note(_FLEET_SCOPE_NOTE, cluster, cluster_configs)
                if note:
                    payload["note"] = note
            return payload


kubernetes_list_pods = KubernetesListPodsTool()


class KubernetesGetPodLogsTool(BaseTool):
    """Fetch recent log lines from a Kubernetes pod container."""

    name = "kubernetes_get_pod_logs"
    source = "kubernetes"
    description = (
        "Fetch recent log lines from one Kubernetes pod (optionally one container). "
        "Require the exact pod_name from kubernetes_list_pods; do not guess names."
    )
    use_cases = [
        "Reading application error logs from a crashing or misbehaving pod",
        "Diagnosing startup failures and misconfigurations via container logs",
        "Collecting evidence of OOM kills, panics, or stack traces",
    ]
    anti_examples = [
        "Do not call before kubernetes_list_pods when the pod name is unknown.",
        "Do not use for CloudWatch or Datadog log search — wrong backend.",
        "Do not use to exec into a pod or mutate cluster state.",
    ]
    surfaces = ("investigation", "chat", "action")
    requires = ["pod_name"]
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
            **_SHARED_KUBECONFIG_PROPS,
            "pod_name": {
                "type": "string",
                "description": "Exact pod name (from kubernetes_list_pods)",
            },
            "container": {
                "type": "string",
                "default": "",
                "description": "Container name (required for multi-container pods)",
            },
            "tail_lines": {
                "type": "integer",
                "default": 100,
                "maximum": KUBERNETES_MAX_LOG_TAIL_LINES,
                "description": "Number of log lines to return from the end of the log (clamped to maximum allowed)",
            },
        },
        "required": ["pod_name"],
    }
    outputs = {
        "lines": "Log lines from the container",
        "total": "Number of log lines returned",
        "truncated": "Whether logs were truncated due to size limits",
        "truncation_note": "Explanation of truncation (only present when truncated=True)",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        pod_name: str,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        container: str = "",
        tail_lines: int = 100,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not pod_name:
            return tool_unavailable(
                "kubernetes",
                "pod_name is required; call kubernetes_list_pods first to find the pod name.",
                lines=[],
                total=0,
                truncated=False,
            )
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable(
                "kubernetes", error or _UNAVAILABLE_MSG, lines=[], total=0, truncated=False
            )
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.get_pod_logs(
                namespace=namespace, pod_name=pod_name, container=container, tail_lines=tail_lines
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes",
                    result.get("error", "unknown error"),
                    lines=[],
                    total=0,
                    truncated=False,
                )
            result_dict = {
                "source": "kubernetes",
                "available": True,
                "pod_name": pod_name,
                "namespace": namespace,
                "container": result.get("container"),
                "lines": result["lines"],
                "total": result["total"],
                "truncated": result["truncated"],
            }

            # Add truncation_note only when truncated is True
            if result.get("truncated") and "truncation_note" in result:
                result_dict["truncation_note"] = result["truncation_note"]

            return result_dict


kubernetes_get_pod_logs = KubernetesGetPodLogsTool()


class KubernetesListDeploymentsTool(BaseTool):
    """List Kubernetes deployments and their replica status."""

    name = "kubernetes_list_deployments"
    source = "kubernetes"
    description = (
        "List deployments in a Kubernetes namespace with their desired, ready, "
        "available, and unavailable replica counts. Use to detect degraded rollouts."
    )
    use_cases = [
        "Checking whether a deployment has unavailable replicas after a rollout",
        "Verifying deployment replica health across a namespace",
        "Identifying deployments stuck in a partial-rollout state",
    ]
    surfaces = ("investigation", "chat")
    requires = []
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
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of deployments to return",
            },
        },
        "required": [],
    }
    outputs = {
        "deployments": "List of deployments with desired/ready/available/unavailable replica counts",
        "total": "Total number of deployments returned",
        "note": "Present only when an empty result may be a single-cluster false negative",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable(
                "kubernetes", error or _UNAVAILABLE_MSG, deployments=[], total=0
            )
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.list_deployments(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), deployments=[], total=0
                )
            payload: dict[str, Any] = {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "deployments": result["deployments"],
                "total": result["total"],
            }
            if result["total"] == 0:
                note = _fleet_scope_note(_FLEET_SCOPE_NOTE, cluster, cluster_configs)
                if note:
                    payload["note"] = note
            return payload


kubernetes_list_deployments = KubernetesListDeploymentsTool()


class KubernetesGetEventsTool(BaseTool):
    """List Kubernetes events for a namespace to diagnose crash loops and scheduling failures."""

    name = "kubernetes_get_events"
    source = "kubernetes"
    description = (
        "List Kubernetes events for a namespace. Events capture crash loops, "
        "OOM kills, image pull failures, and scheduling issues. "
        "Use field_selector to scope events to a specific pod or deployment."
    )
    use_cases = [
        "Diagnosing crash-loop back-off by reading Warning events for a pod",
        "Detecting OOM kills and image pull failures from cluster events",
        "Understanding scheduling failures (Insufficient CPU/Memory)",
        "Correlating event timestamps with incident timeline",
    ]
    surfaces = ("investigation", "chat", "action")
    requires = []
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
            **_SHARED_KUBECONFIG_PROPS,
            "field_selector": {
                "type": "string",
                "default": "",
                "description": (
                    "Field selector to filter events "
                    "(e.g. 'involvedObject.name=my-pod,type=Warning')"
                ),
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of events to return",
            },
        },
        "required": [],
    }
    outputs = {
        "events": "List of events with reason, message, involved object, and timestamps",
        "total": "Total number of events returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        field_selector: str = "",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, events=[], total=0)
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.get_events(
                namespace=namespace, field_selector=field_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), events=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "events": result["events"],
                "total": result["total"],
            }


kubernetes_get_events = KubernetesGetEventsTool()


class KubernetesDescribePodTool(BaseTool):
    """Fetch full spec, status, and container states for a single Kubernetes pod."""

    name = "kubernetes_describe_pod"
    source = "kubernetes"
    description = (
        "Fetch the full spec and status for a single pod: containers, images, resource "
        "requests/limits, environment variables (values redacted, keys only), volume "
        "mounts, conditions, container states, and owner references. Use when list_pods "
        "shows a problem and you need deeper detail on one pod. Preferred over "
        "kubernetes_get_resource for pods specifically — both redact env values "
        "identically, but this tool returns a curated, investigation-shaped view "
        "(container states, owner references) rather than the raw API object. For any "
        "resource type other than pod, use kubernetes_get_resource instead."
    )
    use_cases = [
        "Inspecting container image versions and resource limits on a specific pod",
        "Diagnosing why a pod is stuck in Pending or Init state via detailed conditions",
        "Identifying owner (Deployment, StatefulSet, Job) of a pod",
        "Checking environment variable names (keys only) injected into a container — values are redacted",
    ]
    anti_examples = [
        "Fetching a non-pod resource such as a deployment, service, or node (use "
        + "kubernetes_get_resource)",
        "Listing many pods at once (use kubernetes_list_pods)",
    ]
    surfaces = ("investigation", "chat", "action")
    requires = ["pod_name"]
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
            **_SHARED_KUBECONFIG_PROPS,
            "pod_name": {"type": "string", "description": "Name of the pod to describe"},
        },
        "required": ["pod_name"],
    }
    outputs = {
        "spec": "Pod spec including containers, volumes, node selector, and tolerations",
        "status": "Pod status including phase, conditions, and per-container states",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        pod_name: str,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, spec={}, status={})
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.describe_pod(namespace=namespace, pod_name=pod_name)
            if not result.get("success"):
                error = result.get("error", "unknown error")
                if result.get("not_found"):
                    error += _fleet_scope_note(
                        _FLEET_SCOPE_NOT_FOUND_HINT, cluster, cluster_configs
                    )
                return tool_unavailable("kubernetes", error, spec={}, status={})
            return {
                "source": "kubernetes",
                "available": True,
                **{k: v for k, v in result.items() if k != "success"},
            }


kubernetes_describe_pod = KubernetesDescribePodTool()


class KubernetesListNodesTool(BaseTool):
    """List Kubernetes cluster nodes with conditions and capacity."""

    name = "kubernetes_list_nodes"
    source = "kubernetes"
    description = (
        "List all nodes in the Kubernetes cluster with their readiness conditions, "
        "CPU/memory capacity and allocatable resources, and taints. "
        "Use to diagnose node pressure, NotReady nodes, or scheduling issues."
    )
    use_cases = [
        "Finding nodes in NotReady or MemoryPressure/DiskPressure condition",
        "Checking available allocatable CPU and memory across nodes",
        "Identifying nodes with taints that prevent pod scheduling",
        "Correlating pod scheduling failures with node capacity",
    ]
    surfaces = ("investigation", "chat", "action")
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "cluster_configs"]
    input_schema = {
        "type": "object",
        "properties": {
            **_CLUSTER_SCOPED_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of nodes to return",
            },
        },
        "required": [],
    }
    outputs = {
        "nodes": "List of nodes with conditions, capacity, allocatable resources, and taints",
        "total": "Total number of nodes returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        cluster: str = "",
        cluster_configs: dict[str, Any] | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, _conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": DEFAULT_KUBERNETES_NAMESPACE,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, nodes=[], total=0)
        with client:
            result = client.list_nodes(limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), nodes=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "nodes": result["nodes"],
                "total": result["total"],
            }


kubernetes_list_nodes = KubernetesListNodesTool()


class KubernetesListServicesTool(BaseTool):
    """List Kubernetes services with their type, ports, and selector."""

    name = "kubernetes_list_services"
    source = "kubernetes"
    description = (
        "List services in a Kubernetes namespace with their type (ClusterIP/NodePort/LoadBalancer), "
        "clusterIP, external IPs, port mappings, and pod selector. "
        "Use to diagnose connectivity issues or verify service routing."
    )
    use_cases = [
        "Checking which pods a service routes to via its selector",
        "Verifying LoadBalancer external IP assignment",
        "Diagnosing port mismatches between services and pods",
        "Finding services exposed via NodePort for debugging",
    ]
    surfaces = ("investigation", "chat", "action")
    requires = []
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
            **_SHARED_KUBECONFIG_PROPS,
            "label_selector": {
                "type": "string",
                "default": "",
                "description": "Label selector filter (e.g. 'app=my-service')",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of services to return",
            },
        },
        "required": [],
    }
    outputs = {
        "services": "List of services with type, clusterIP, ports, and selector",
        "total": "Total number of services returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        label_selector: str = "",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, services=[], total=0)
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.list_services(
                namespace=namespace, label_selector=label_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), services=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "services": result["services"],
                "total": result["total"],
            }


kubernetes_list_services = KubernetesListServicesTool()


class KubernetesListStatefulSetsTool(BaseTool):
    """List Kubernetes StatefulSets with replica status."""

    name = "kubernetes_list_statefulsets"
    source = "kubernetes"
    description = (
        "List StatefulSets in a Kubernetes namespace with desired, ready, current, "
        "and updated replica counts. Use to detect degraded or stalled StatefulSet rollouts."
    )
    use_cases = [
        "Checking whether a StatefulSet has unavailable replicas",
        "Diagnosing stuck StatefulSet rolling updates",
        "Verifying database or stateful service replica health",
    ]
    surfaces = ("investigation", "chat")
    requires = []
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
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of StatefulSets to return",
            },
        },
        "required": [],
    }
    outputs = {
        "statefulsets": "List of StatefulSets with desired/ready/current/updated replica counts",
        "total": "Total number of StatefulSets returned",
        "note": "Present only when an empty result may be a single-cluster false negative",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable(
                "kubernetes", error or _UNAVAILABLE_MSG, statefulsets=[], total=0
            )
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.list_statefulsets(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), statefulsets=[], total=0
                )
            payload: dict[str, Any] = {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "statefulsets": result["statefulsets"],
                "total": result["total"],
            }
            if result["total"] == 0:
                note = _fleet_scope_note(_FLEET_SCOPE_NOTE, cluster, cluster_configs)
                if note:
                    payload["note"] = note
            return payload


kubernetes_list_statefulsets = KubernetesListStatefulSetsTool()


class KubernetesListDaemonSetsTool(BaseTool):
    """List Kubernetes DaemonSets with desired/ready/available counts."""

    name = "kubernetes_list_daemonsets"
    source = "kubernetes"
    description = (
        "List DaemonSets in a Kubernetes namespace with desired, current, ready, "
        "up-to-date, and available counts per node. "
        "Use to diagnose node-agent or logging/monitoring DaemonSet issues."
    )
    use_cases = [
        "Checking whether a DaemonSet is running on all expected nodes",
        "Diagnosing nodes where a DaemonSet pod is not scheduled or not ready",
        "Verifying a DaemonSet update has rolled out to all nodes",
    ]
    surfaces = ("investigation", "chat")
    requires = []
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
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of DaemonSets to return",
            },
        },
        "required": [],
    }
    outputs = {
        "daemonsets": "List of DaemonSets with desired/current/ready/up_to_date/available counts",
        "total": "Total number of DaemonSets returned",
        "note": "Present only when an empty result may be a single-cluster false negative",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, daemonsets=[], total=0)
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.list_daemonsets(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), daemonsets=[], total=0
                )
            payload: dict[str, Any] = {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "daemonsets": result["daemonsets"],
                "total": result["total"],
            }
            if result["total"] == 0:
                note = _fleet_scope_note(_FLEET_SCOPE_NOTE, cluster, cluster_configs)
                if note:
                    payload["note"] = note
            return payload


kubernetes_list_daemonsets = KubernetesListDaemonSetsTool()


class KubernetesListIngressesTool(BaseTool):
    """List Kubernetes Ingress resources with routing rules and TLS config."""

    name = "kubernetes_list_ingresses"
    source = "kubernetes"
    description = (
        "List Ingress resources in a Kubernetes namespace with their host rules, "
        "path-to-service mappings, TLS configuration, and load balancer status. "
        "Use to diagnose HTTP routing misconfigurations."
    )
    use_cases = [
        "Checking which service an ingress path routes to",
        "Verifying TLS certificate secret names and covered hosts",
        "Finding load balancer IPs or hostnames assigned to an ingress",
        "Diagnosing 404 or routing issues in HTTP-based services",
    ]
    surfaces = ("investigation", "chat")
    requires = []
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
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of Ingresses to return",
            },
        },
        "required": [],
    }
    outputs = {
        "ingresses": "List of Ingresses with host rules, path-service mappings, TLS, and LB status",
        "total": "Total number of Ingresses returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, ingresses=[], total=0)
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.list_ingresses(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), ingresses=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "ingresses": result["ingresses"],
                "total": result["total"],
            }


kubernetes_list_ingresses = KubernetesListIngressesTool()


class KubernetesListConfigMapsTool(BaseTool):
    """List Kubernetes ConfigMaps with their key-value data."""

    name = "kubernetes_list_configmaps"
    source = "kubernetes"
    description = (
        "List ConfigMaps in a Kubernetes namespace with their full key-value data. "
        "Use to inspect application configuration, verify environment variable sources, "
        "or check for misconfigured settings."
    )
    use_cases = [
        "Inspecting application configuration values injected via ConfigMap",
        "Verifying a ConfigMap has the expected keys and values after a deploy",
        "Diagnosing misconfigured endpoints, feature flags, or environment settings",
    ]
    surfaces = ("investigation", "chat")
    requires = []
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
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of ConfigMaps to return",
            },
        },
        "required": [],
    }
    outputs = {
        "configmaps": "List of ConfigMaps with their data key-value pairs",
        "total": "Total number of ConfigMaps returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, configmaps=[], total=0)
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.list_configmaps(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), configmaps=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "configmaps": result["configmaps"],
                "total": result["total"],
            }


kubernetes_list_configmaps = KubernetesListConfigMapsTool()


class KubernetesGetResourceTool(BaseTool):
    """Fetch a single named Kubernetes resource by type and name."""

    name = "kubernetes_get_resource"
    source = "kubernetes"
    description = (
        "Fetch the raw spec and status of a single named Kubernetes resource, "
        "equivalent to `kubectl get <type> <name> -o json`. Supports: pod, deployment, "
        "statefulset, daemonset, service, ingress, configmap, replicaset, "
        "persistentvolumeclaim (pvc), and node. Env variable values are redacted (keys "
        "only) for pod and workload types (deployment, statefulset, daemonset, "
        "replicaset), same as kubernetes_describe_pod. For POD detail specifically, "
        "prefer kubernetes_describe_pod instead — it returns the same redacted data in "
        "a curated, investigation-shaped view (container states, owner references) "
        "rather than the raw API object. The namespace field is ignored for "
        "cluster-scoped types like node."
    )
    use_cases = [
        "Fetching the full YAML-equivalent of any non-pod named resource for deep inspection",
        "Reading a specific deployment's full spec including strategy and selector",
        "Inspecting a PVC's storage class, capacity, and bound status",
        "Getting the full node spec to check kubelet version and OS image",
    ]
    anti_examples = [
        "Inspecting a pod's containers, conditions, or env var keys (use "
        + "kubernetes_describe_pod — same detail, redacts env values)",
        "Listing multiple resources or filtering by label/field selector (use the "
        + "matching kubernetes_list_* tool instead)",
    ]
    surfaces = ("investigation", "chat")
    requires = ["resource_type", "name"]
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
            **_SHARED_KUBECONFIG_PROPS,
            "resource_type": {
                "type": "string",
                "enum": _RESOURCE_TYPE_ENUM,
                "description": "Kubernetes resource type to fetch.",
            },
            "name": {
                "type": "string",
                "description": "Name of the resource to fetch",
            },
        },
        "required": ["resource_type", "name"],
    }
    outputs = {
        "resource": "Full resource object as a dict (equivalent to kubectl get -o json)",
        "resource_type": "The resource type that was fetched",
        "name": "The resource name that was fetched",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        resource_type: str,
        name: str,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable(
                "kubernetes",
                error or _UNAVAILABLE_MSG,
                resource={},
                resource_type=resource_type,
                name=name,
            )
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.get_resource(
                resource_type=resource_type, name=name, namespace=namespace
            )
            if not result.get("success"):
                error = result.get("error", "unknown error")
                if result.get("not_found"):
                    error += _fleet_scope_note(
                        _FLEET_SCOPE_NOT_FOUND_HINT, cluster, cluster_configs
                    )
                return tool_unavailable(
                    "kubernetes",
                    error,
                    resource={},
                    resource_type=resource_type,
                    name=name,
                )
            return {
                "source": "kubernetes",
                "available": True,
                "resource_type": result["resource_type"],
                "name": result["name"],
                "resource": result["resource"],
            }


kubernetes_get_resource = KubernetesGetResourceTool()


class KubernetesListClustersTool(BaseTool):
    """List the registered Kubernetes clusters that can be targeted by name."""

    name = "kubernetes_list_clusters"
    source = "kubernetes"
    description = (
        "List the registered Kubernetes clusters (instances) you can investigate. "
        "Returns each cluster's name and tags; pass a returned name as the 'cluster' "
        "argument to any other kubernetes_* tool to target that specific cluster (for "
        "example a specific GKE cluster in a specific GCP project). Call this first "
        "when more than one cluster may be configured."
    )
    use_cases = [
        "Discovering which Kubernetes clusters/projects are configured before investigating",
        "Choosing the right cluster to target when several are registered",
        "Confirming a named cluster exists before calling other kubernetes_* tools",
    ]
    surfaces = ("investigation", "chat", "action")
    requires = []
    injected_params = ["clusters"]
    input_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    outputs = {
        "clusters": "List of registered clusters with name, tags, and is_default flag",
        "total": "Total number of registered clusters",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return {"clusters": _clusters_list(sources)}

    def run(
        self,
        clusters: list[dict[str, Any]] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        clusters = clusters or []
        return {
            "source": "kubernetes",
            "available": True,
            "clusters": clusters,
            "total": len(clusters),
        }


class KubernetesListNamespacesTool(BaseTool):
    """List the namespaces in a Kubernetes cluster."""

    name = "kubernetes_list_namespaces"
    source = "kubernetes"
    description = (
        "List the namespaces in a Kubernetes cluster. Call this before "
        "listing pods or deployments when you do not already know which namespace "
        "holds the workload — the cluster's configured default namespace is often "
        "empty."
    )
    use_cases = [
        "Discovering where a named service runs",
        "Confirming an environment namespace exists before scoping a query",
        'Checking whether an empty pod list means "healthy" or "wrong namespace"',
    ]
    anti_examples = [
        "Listing pods — use kubernetes_list_pods",
        "Listing registered clusters — use kubernetes_list_clusters",
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
            **_CLUSTER_SCOPED_PROPS,
            "limit": {
                "type": "integer",
                "default": 200,
                "description": "Maximum number of namespaces to return",
            },
        },
        "required": [],
    }
    outputs = {
        "namespaces": "List of namespaces with status, labels, and creation timestamp",
        "total": "Total number of namespaces returned",
        "listable": "Whether the credential can enumerate namespaces cluster-wide",
        "configured_namespace": "The cluster's configured default namespace",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        limit: int = 200,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable(
                "kubernetes", error or _UNAVAILABLE_MSG, namespaces=[], total=0, listable=False
            )
        configured_namespace = conn.get("namespace") or DEFAULT_KUBERNETES_NAMESPACE
        with client:
            result = client.list_namespaces(limit=limit)
            if not result.get("success"):
                # Check for 403/401 to enable graceful degradation
                if result.get("forbidden"):
                    return {
                        "source": "kubernetes",
                        "available": True,
                        "listable": False,
                        "namespaces": [],
                        "total": 0,
                        "configured_namespace": configured_namespace,
                        "note": (
                            "The credential cannot enumerate namespaces cluster-wide. "
                            "The namespace must come from the user or from configured_namespace. "
                            "This limits enumeration only; namespaced tools still work when a "
                            "namespace is named explicitly."
                        ),
                    }
                return tool_unavailable(
                    "kubernetes",
                    result.get("error", "unknown error"),
                    namespaces=[],
                    total=0,
                    listable=False,
                )
            return {
                "source": "kubernetes",
                "available": True,
                "listable": True,
                "namespaces": result["namespaces"],
                "total": result["total"],
                "configured_namespace": configured_namespace,
            }


class KubernetesListWorkloadsTool(BaseTool):
    """List every workload in a Kubernetes namespace regardless of kind."""

    name = "kubernetes_list_workloads"
    source = "kubernetes"
    description = (
        "List every workload in a Kubernetes namespace regardless of kind: Deployments, "
        "StatefulSets, DaemonSets, CronJobs, and Argo Rollouts, in one table. Prefer this "
        "over the kind-specific listers when asked whether a workload exists or is healthy — "
        "a service may be an Argo Rollout rather than a Deployment, and kubernetes_list_deployments "
        "returns nothing for it. ReplicaSets are omitted: they are intermediate objects owned by "
        "a Deployment or Rollout. Kinds the cluster does not run or the credential cannot read "
        "are reported in unavailable_kinds rather than silently dropped, so an empty result for "
        "a kind means 'none exist', not 'not checked'."
    )
    use_cases = [
        "Checking whether a named service exists in a namespace, whatever kind it is",
        "Triaging a namespace where workloads are Argo Rollouts rather than Deployments",
        "Finding degraded workloads across every kind in one call",
        "Confirming a workload is absent before reporting that nothing was found",
    ]
    anti_examples = [
        "Listing pods or checking restart counts (use kubernetes_list_pods)",
        "Reading Rollout strategy, step, or revision detail (use kubernetes_list_rollouts)",
        "Locating a workload when you do not know which cluster runs it (use kubernetes_search_fleet)",
    ]
    surfaces = ("investigation", "chat", "action")
    requires = []
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
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of workloads to return per kind",
            },
        },
        "required": [],
    }
    outputs = {
        "workloads": "One row per workload with kind, name, ready/desired counts, and phase",
        "total": "Number of workload rows returned",
        "truncated": "True when at least one kind had more workloads than the limit returned",
        "truncated_kinds": "Kinds whose list was cut short by the limit",
        "unavailable_kinds": "Kinds that could not be listed, with the reason (e.g. CRD absent)",
        "note": "Present only when an empty result may be a single-cluster false negative",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, workloads=[], total=0)
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.list_workloads(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), workloads=[], total=0
                )
            payload: dict[str, Any] = {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "workloads": result["workloads"],
                "total": result["total"],
                "truncated": result["truncated"],
                "truncated_kinds": result["truncated_kinds"],
                "unavailable_kinds": result["unavailable_kinds"],
            }
            if result["total"] == 0 and len(result["unavailable_kinds"]) < _WORKLOAD_KIND_COUNT:
                note = _fleet_scope_note(_FLEET_SCOPE_NOTE, cluster, cluster_configs)
                if note:
                    payload["note"] = note
            return payload


class KubernetesListRolloutsTool(BaseTool):
    """List Argo Rollouts with strategy, phase, step, and revision state."""

    name = "kubernetes_list_rollouts"
    source = "kubernetes"
    description = (
        "List Argo Rollouts in a Kubernetes namespace with their strategy, phase, step, "
        "pause state, and revision information. Use to diagnose Argo Rollout deployments, "
        "check promotion status, and understand canary/blue-green strategy state."
    )
    use_cases = [
        "Checking if a Rollout is paused and awaiting promotion",
        "Diagnosing stuck or failed Argo Rollout deployments",
        "Understanding canary step progression and timing",
        "Verifying blue-green strategy switching state",
    ]
    anti_examples = [
        "Listing regular Deployments (use kubernetes_list_deployments)",
        "Checking pod status (use kubernetes_list_pods)",
        "Finding workloads of unknown type (use kubernetes_list_workloads)",
    ]
    surfaces = ("investigation", "chat")
    requires = []
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
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of Rollouts to return",
            },
        },
        "required": [],
    }
    outputs = {
        "rollouts": "Rollouts with strategy, phase, step, pause state, and revisions",
        "total": "Number of Rollouts returned",
        "truncated": "True when more Rollouts exist than the limit returned",
        "crd_installed": "False when the cluster does not run Argo Rollouts",
        "note": "Present when the CRD is absent, or when an empty result may be a single-cluster false negative",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _base_params(sources)

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "",
        cluster: str = "",
        default_namespace: str = "",
        cluster_configs: dict[str, Any] | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": default_namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, rollouts=[], total=0)
        namespace = _effective_namespace(namespace, conn)
        with client:
            result = client.list_rollouts(namespace=namespace, limit=limit)
            if result.get("kind_unavailable"):
                return {
                    "source": "kubernetes",
                    "available": True,
                    "crd_installed": False,
                    "rollouts": [],
                    "total": 0,
                    "namespace": namespace,
                    "note": (
                        "This cluster does not expose Argo Rollouts, or the credential "
                        "cannot read them. That is not evidence the workload is missing — "
                        "use kubernetes_list_workloads to see the kinds that are readable."
                    ),
                }
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), rollouts=[], total=0
                )
            payload: dict[str, Any] = {
                "source": "kubernetes",
                "available": True,
                "crd_installed": True,
                "namespace": namespace,
                "rollouts": result["rollouts"],
                "total": result["total"],
                "truncated": result["truncated"],
            }
            if result["total"] == 0:
                note = _fleet_scope_note(_FLEET_SCOPE_NOTE, cluster, cluster_configs)
                if note:
                    payload["note"] = note
            return payload


kubernetes_list_namespaces = KubernetesListNamespacesTool()

kubernetes_list_clusters = KubernetesListClustersTool()

kubernetes_list_workloads = KubernetesListWorkloadsTool()

kubernetes_list_rollouts = KubernetesListRolloutsTool()
