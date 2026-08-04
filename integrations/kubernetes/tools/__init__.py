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

from core.tool_framework.base import BaseTool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations import selectors
from integrations.config_models import KubernetesIntegrationConfig
from integrations.kubernetes.client import _RESOURCE_DISPATCH, KubernetesClient

_RESOURCE_TYPE_ENUM: list[str] = sorted(_RESOURCE_DISPATCH.keys())

_UNAVAILABLE_MSG = "Kubernetes integration is not configured (missing kubeconfig)."


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

    ``cluster_configs`` is a passthrough (never listed in ``injected_params``,
    so it is not protected and the model never sends it): ``run`` uses it to
    resolve an LLM-chosen ``cluster`` name to that instance's connection fields.
    """
    k8s = sources.get("kubernetes", {})
    return {
        "kubeconfig": k8s.get("kubeconfig", ""),
        "kubeconfig_path": k8s.get("kubeconfig_path", ""),
        "context": k8s.get("context", ""),
        "namespace": k8s.get("namespace", "default"),
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


_CLUSTER_PROP: dict[str, Any] = {
    "type": "string",
    "default": "",
    "description": (
        "Registered Kubernetes cluster/instance to target (see "
        "kubernetes_list_clusters). Omit to use the default cluster."
    ),
}

_SHARED_KUBECONFIG_PROPS: dict[str, Any] = {
    "kubeconfig": {"type": "string", "description": "Raw kubeconfig YAML string"},
    "kubeconfig_path": {
        "type": "string",
        "default": "",
        "description": "Path to kubeconfig file (alternative to kubeconfig)",
    },
    "context": {"type": "string", "default": "", "description": "Kubeconfig context to use"},
    "namespace": {
        "type": "string",
        "default": "default",
        "description": "Kubernetes namespace to target",
    },
    "cluster": _CLUSTER_PROP,
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
    surfaces = ("investigation", "chat")
    requires = ["kubeconfig"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, pods=[], total=0)
        namespace = conn.get("namespace", "default") or "default"
        with client:
            result = client.list_pods(
                namespace=namespace, label_selector=label_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), pods=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "pods": result["pods"],
                "total": result["total"],
            }


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
    surfaces = ("investigation", "chat")
    requires = ["pod_name"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
                "description": "Number of log lines to return from the end of the log",
            },
        },
        "required": ["pod_name"],
    }
    outputs = {
        "lines": "Log lines from the container",
        "total": "Number of log lines returned",
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
        namespace: str = "default",
        cluster: str = "",
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
            )
        client, conn, error = _resolve_client(
            cluster,
            cluster_configs,
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": context,
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, lines=[], total=0)
        namespace = conn.get("namespace", "default") or "default"
        with client:
            result = client.get_pod_logs(
                namespace=namespace, pod_name=pod_name, container=container, tail_lines=tail_lines
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), lines=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "pod_name": pod_name,
                "namespace": namespace,
                "container": result.get("container"),
                "lines": result["lines"],
                "total": result["total"],
            }


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
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable(
                "kubernetes", error or _UNAVAILABLE_MSG, deployments=[], total=0
            )
        namespace = conn.get("namespace", "default") or "default"
        with client:
            result = client.list_deployments(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), deployments=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "deployments": result["deployments"],
                "total": result["total"],
            }


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
    surfaces = ("investigation", "chat")
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, events=[], total=0)
        namespace = conn.get("namespace", "default") or "default"
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
    surfaces = ("investigation", "chat")
    requires = ["pod_name"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, spec={}, status={})
        namespace = conn.get("namespace", "default") or "default"
        with client:
            result = client.describe_pod(namespace=namespace, pod_name=pod_name)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), spec={}, status={}
                )
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
    surfaces = ("investigation", "chat")
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context"]
    input_schema = {
        "type": "object",
        "properties": {
            "kubeconfig": {"type": "string", "description": "Raw kubeconfig YAML string"},
            "kubeconfig_path": {
                "type": "string",
                "default": "",
                "description": "Path to kubeconfig file (alternative to kubeconfig)",
            },
            "context": {
                "type": "string",
                "default": "",
                "description": "Kubeconfig context to use",
            },
            "cluster": _CLUSTER_PROP,
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
                "namespace": "default",
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
    surfaces = ("investigation", "chat")
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, services=[], total=0)
        namespace = conn.get("namespace", "default") or "default"
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
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable(
                "kubernetes", error or _UNAVAILABLE_MSG, statefulsets=[], total=0
            )
        namespace = conn.get("namespace", "default") or "default"
        with client:
            result = client.list_statefulsets(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), statefulsets=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "statefulsets": result["statefulsets"],
                "total": result["total"],
            }


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
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, daemonsets=[], total=0)
        namespace = conn.get("namespace", "default") or "default"
        with client:
            result = client.list_daemonsets(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), daemonsets=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "daemonsets": result["daemonsets"],
                "total": result["total"],
            }


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
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, ingresses=[], total=0)
        namespace = conn.get("namespace", "default") or "default"
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
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
            },
        )
        if client is None:
            return tool_unavailable("kubernetes", error or _UNAVAILABLE_MSG, configmaps=[], total=0)
        namespace = conn.get("namespace", "default") or "default"
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
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
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
        namespace: str = "default",
        cluster: str = "",
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
                "namespace": namespace,
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
        namespace = conn.get("namespace", "default") or "default"
        with client:
            result = client.get_resource(
                resource_type=resource_type, name=name, namespace=namespace
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes",
                    result.get("error", "unknown error"),
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
    surfaces = ("investigation", "chat")
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


kubernetes_list_clusters = KubernetesListClustersTool()
