"""Cloud-OpsBench cache-backed Kubernetes tools.

Replays Cloud-OpsBench (Wang et al., arXiv:2603.00468) actions against the
per-case ``tool_cache.json`` instead of talking to a real EKS cluster.
The tools are gated on the presence of a backend at
``sources["eks"]["_bench_backend"]`` — a slot the bench adapter sets and
production code never populates. The real EKS tools take over in any
non-bench context.

Each ``@tool`` declaration sets ``injected_params=("cloudops_backend",)``
so the replay backend is hidden from the LLM's tool-call schema and
supplied at call time by ``extract_params``. Without that, the LLM would
treat ``cloudops_backend`` as a free-text param and dispatch's
``{**injected, **tc.input}`` merge would let the LLM string override the
real backend, crashing every call with
``'str' object has no attribute '<Action>'``.

The ``extract_params`` callbacks pre-fill positional args from the case's
recorded ``process`` steps. After the injected-params fix landed these
prefills are mostly dead-code — the LLM owns the real values via
``tc.input`` — but they still serve as a sane-default safety net when the
LLM omits a required param.

CloudOpsBench dataset conventions encoded here:
- ``case.process`` is split into ``path1`` (alert trigger sequence) and
  ``path2`` (recovery / diagnostic actions).
- Each process step is encoded as ``"Action::param1::param2::..."``.
- ``case.result.fault_object`` is encoded as ``"app/<service_name>"``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from app.tools.tool_decorator import tool

# --------------------------------------------------------------------------- #
# Dataset conventions — change only when the upstream dataset format changes. #
# --------------------------------------------------------------------------- #

# Process-step actions that encode service_name in position [1].
# The dataset guarantees this contract for these four action names.
_ACTIONS_WITH_SERVICE_NAME: frozenset[str] = frozenset(
    {
        "GetErrorLogs",
        "GetRecentLogs",
        "GetServiceDependencies",
        "GetAppYAML",
    }
)

# Prefix used in ``case.result.fault_object``: ``"app/<service_name>"``.
_FAULT_OBJECT_APP_PREFIX = "app/"

# Search order over ``case.process``. The asymmetry is intentional:
# - alert-first: when we want the affected service, path1 names it
# - recovery-first: when we want action parameters, path2 has the calls
_PATHS_ALERT_FIRST: tuple[str, ...] = ("path1", "path2")
_PATHS_RECOVERY_FIRST: tuple[str, ...] = ("path2", "path1")

# --------------------------------------------------------------------------- #
# Fallback defaults — dead-code on the happy path.                            #
#                                                                             #
# After the injected-params fix the LLM is the source of truth for every      #
# non-injected tool arg via ``tc.input``. These constants only fire when      #
# BOTH the case process is missing the relevant step AND the LLM omits the    #
# required param — a combination that should not happen for required fields. #
# Kept as a safety net, not as primary behavior.                              #
# --------------------------------------------------------------------------- #

_DEFAULT_SERVICE = "frontend"  # most-frequent service in the dataset
_DEFAULT_NAMESPACE = "default"  # Kubernetes' standard namespace
_DEFAULT_RESOURCE_TYPE = "pods"  # most-listed K8s resource type
_DEFAULT_DESCRIBE_RESOURCE_TYPE = "services"
_DEFAULT_HTTP_PORT = 80
_DEFAULT_CONTROL_PLANE_NODE = "master"  # legacy K8s naming used by the dataset
_DEFAULT_CONTROL_PLANE_SERVICE = "kube-scheduler"


class _CloudOpsBenchBackend(Protocol):
    """Duck-typed contract for the Cloud-OpsBench replay backend.

    The concrete implementation lives at
    ``tests/benchmarks/cloudopsbench/replay_backend.py``. Capturing the
    contract here instead of importing the class keeps ``app/`` runtime
    code free of a dependency on ``tests/``.

    Identification is by **dedicated source slot**, not a marker attribute:
    the bench adapter sets ``sources["eks"]["_bench_backend"]`` (distinct
    from the synthetic-test ``_backend`` slot), so production tool
    availability checks stay completely unaware of bench backend types.
    No ``is_cloudopsbench_backend`` flag needed.
    """

    # The Cloud-OpsBench dataset case being replayed. Typed ``Any`` because
    # the Case schema lives outside ``app/`` (see
    # ``tests/benchmarks/cloudopsbench/case_loader.py``). Attributes consumed
    # here: ``case.process`` (dict of path1/path2 step lists) and
    # ``case.result.fault_object``.
    case: Any

    # Default K8s namespace recorded on the case. Last-resort fallback in
    # ``_default_namespace`` when neither alert sources nor the case
    # override it.
    default_namespace: str


def _cloudops_backend(sources: dict[str, dict]) -> Any:
    """Look up the CloudOpsBench replay backend in its dedicated slot.

    The bench adapter sets ``sources["eks"]["_bench_backend"]`` (not
    ``_backend``) deliberately: ``_backend`` is the slot for synthetic-test
    fixture backends that share the EKS tool API, and the replay backend
    speaks a different (paper-protocol) API. Using a separate slot means
    production tools that read ``_backend`` (``_eks_available``,
    ``eks_available_or_backend``) stay completely unaware of bench
    backends — no provider-specific branching needed in their availability
    checks.
    """
    return (sources.get("eks") or {}).get("_bench_backend")


def _cloudops_available(sources: dict[str, dict]) -> bool:
    return _cloudops_backend(sources) is not None


def _service_from_process(backend: Any) -> str:
    case = getattr(backend, "case", None)
    process = getattr(case, "process", {}) or {}
    for path_name in _PATHS_ALERT_FIRST:
        for step in process.get(path_name, []):
            if not isinstance(step, str):
                continue
            parts = step.split("::")
            if len(parts) >= 2 and parts[0] in _ACTIONS_WITH_SERVICE_NAME:
                return parts[1]

    result = getattr(case, "result", None)
    fault_object = getattr(result, "fault_object", "")
    if isinstance(fault_object, str) and fault_object.startswith(_FAULT_OBJECT_APP_PREFIX):
        return fault_object.split("/", 1)[1]
    return _DEFAULT_SERVICE


def _process_parts_for_action(backend: Any, action_name: str) -> list[str]:
    case = getattr(backend, "case", None)
    process = getattr(case, "process", {}) or {}
    for path_name in _PATHS_RECOVERY_FIRST:
        for step in process.get(path_name, []):
            if not isinstance(step, str):
                continue
            parts = step.split("::")
            if parts and parts[0] == action_name:
                return parts
    return []


def _resource_type_from_process(backend: Any) -> str:
    parts = _process_parts_for_action(backend, "GetResources")
    if len(parts) >= 2:
        return parts[1]
    return _DEFAULT_RESOURCE_TYPE


def _default_namespace(backend: Any, sources: dict[str, dict]) -> str:
    eks = sources.get("eks") or {}
    namespace = eks.get("namespace") or getattr(backend, "default_namespace", "")
    return str(namespace or _DEFAULT_NAMESPACE)


def _extract_backend(sources: dict[str, dict]) -> dict[str, Any]:
    return {"cloudops_backend": _cloudops_backend(sources)}


def _extract_get_resources(sources: dict[str, dict]) -> dict[str, Any]:
    backend = _cloudops_backend(sources)
    return {
        "cloudops_backend": backend,
        "resource_type": _resource_type_from_process(backend),
        "namespace": _default_namespace(backend, sources),
    }


def _extract_describe_resource(sources: dict[str, dict]) -> dict[str, Any]:
    backend = _cloudops_backend(sources)
    parts = _process_parts_for_action(backend, "DescribeResource")
    resource_type = parts[1] if len(parts) >= 2 else _DEFAULT_DESCRIBE_RESOURCE_TYPE
    name = parts[2] if len(parts) >= 3 else _service_from_process(backend)
    return {
        "cloudops_backend": backend,
        "resource_type": resource_type,
        "name": name,
        "namespace": _default_namespace(backend, sources),
    }


def _extract_error_logs(sources: dict[str, dict]) -> dict[str, Any]:
    backend = _cloudops_backend(sources)
    parts = _process_parts_for_action(backend, "GetErrorLogs")
    return {
        "cloudops_backend": backend,
        "namespace": _default_namespace(backend, sources),
        "service_name": parts[1] if len(parts) >= 2 else _service_from_process(backend),
    }


def _extract_recent_logs(sources: dict[str, dict]) -> dict[str, Any]:
    backend = _cloudops_backend(sources)
    parts = _process_parts_for_action(backend, "GetRecentLogs")
    return {
        "cloudops_backend": backend,
        "namespace": _default_namespace(backend, sources),
        "service_name": parts[1] if len(parts) >= 2 else _service_from_process(backend),
    }


def _extract_app_yaml(sources: dict[str, dict]) -> dict[str, Any]:
    backend = _cloudops_backend(sources)
    parts = _process_parts_for_action(backend, "GetAppYAML")
    return {
        "cloudops_backend": backend,
        "app_name": parts[1] if len(parts) >= 2 else _service_from_process(backend),
    }


def _extract_service_dependencies(sources: dict[str, dict]) -> dict[str, Any]:
    backend = _cloudops_backend(sources)
    parts = _process_parts_for_action(backend, "GetServiceDependencies")
    return {
        "cloudops_backend": backend,
        "service_name": parts[1] if len(parts) >= 2 else _service_from_process(backend),
    }


def _extract_connectivity(sources: dict[str, dict]) -> dict[str, Any]:
    backend = _cloudops_backend(sources)
    parts = _process_parts_for_action(backend, "CheckServiceConnectivity")
    return {
        "cloudops_backend": backend,
        "service_name": parts[1] if len(parts) >= 2 else _service_from_process(backend),
        "port": int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else _DEFAULT_HTTP_PORT,
        "namespace": _default_namespace(backend, sources),
    }


def _extract_node_status(sources: dict[str, dict]) -> dict[str, Any]:
    backend = _cloudops_backend(sources)
    parts = _process_parts_for_action(backend, "CheckNodeServiceStatus")
    return {
        "cloudops_backend": backend,
        "node_name": parts[1] if len(parts) >= 2 else _DEFAULT_CONTROL_PLANE_NODE,
        "service_name": parts[2] if len(parts) >= 3 else _DEFAULT_CONTROL_PLANE_SERVICE,
    }


def _run_backend(cloudops_backend: Any, method_name: str, **kwargs: Any) -> dict[str, Any]:
    if cloudops_backend is None:
        return {
            "source": "cloudopsbench",
            "available": False,
            "error": "CloudOpsBench replay backend is not available.",
        }
    method = cast(Callable[..., dict[str, Any]], getattr(cloudops_backend, method_name))
    return method(**kwargs)


@tool(
    name="GetResources",
    source="eks",
    description=(
        "List Kubernetes resources in the cluster — pods, deployments, "
        "services, events, nodes, replicasets. Use this FIRST in most "
        "investigations to identify which workloads are failing, see "
        "pod status (CrashLoopBackOff, ImagePullBackOff, Pending, "
        "ContainerCreating), and find recent events that indicate why."
    ),
    use_cases=[
        "Identify which pods are unhealthy: resource_type='pods' shows STATUS column",
        "Find broken deployments: resource_type='deployments' shows READY vs DESIRED replicas",
        "Discover failure signals: resource_type='events' shows scheduling errors, image pull failures, OOM kills, secret-binding errors",
        "Enumerate services and their selectors: resource_type='services'",
        "Check node health: resource_type='nodes' shows Ready / NotReady / SchedulingDisabled",
    ],
    requires=["cluster_name"],
    input_schema={"type": "object", "properties": {"resource_type": {"type": "string"}}},
    is_available=_cloudops_available,
    extract_params=_extract_get_resources,
    injected_params=("cloudops_backend",),
)
def get_resources(
    cloudops_backend: Any,
    resource_type: str,
    namespace: str = "",
    name: str | None = None,
    show_labels: bool = False,
    output_wide: bool = False,
    label_selector: str | None = None,
) -> dict[str, Any]:
    return _run_backend(
        cloudops_backend,
        "GetResources",
        resource_type=resource_type,
        namespace=namespace,
        name=name,
        show_labels=show_labels,
        output_wide=output_wide,
        label_selector=label_selector,
    )


@tool(
    name="DescribeResource",
    source="eks",
    description=(
        "Get detailed configuration for a specific named Kubernetes resource "
        "(pod, deployment, service, statefulset). Use AFTER GetResources "
        "to investigate WHY a specific workload is failing — shows env "
        "vars, secret references, volume mounts, container ports, "
        "image tags, and the full status with event log."
    ),
    use_cases=[
        "Inspect a failing pod's env vars and secret references: resource_type='pod', name='<pod-name>'",
        "Check a deployment's image, replica count, and selectors: resource_type='deployment', name='<deployment>'",
        "Verify a service's port mappings, selectors, and endpoints: resource_type='service', name='<service>'",
        "Examine a StatefulSet's volume claims and pod template: resource_type='statefulset', name='<sts-name>'",
    ],
    requires=["cluster_name"],
    is_available=_cloudops_available,
    extract_params=_extract_describe_resource,
    injected_params=("cloudops_backend",),
)
def describe_resource(
    cloudops_backend: Any,
    resource_type: str,
    name: str,
    namespace: str = "",
) -> dict[str, Any]:
    return _run_backend(
        cloudops_backend,
        "DescribeResource",
        resource_type=resource_type,
        name=name,
        namespace=namespace,
    )


@tool(
    name="GetClusterConfiguration",
    source="eks",
    description=(
        "Get cluster-level state: node health, control-plane component "
        "status (kubelet, kube-scheduler, kube-proxy, containerd), and "
        "system-level conditions. Use when issues appear cluster-wide "
        "rather than workload-specific — e.g. multiple unrelated services "
        "failing simultaneously, or scheduling failures across namespaces."
    ),
    use_cases=[
        "Detect node-level problems: which nodes are NotReady, cordoned, or out of resources",
        "Diagnose control-plane issues: kubelet down, scheduler offline, containerd crashed, kube-proxy unavailable",
        "Establish baseline cluster health before narrowing to a specific workload",
    ],
    requires=["cluster_name"],
    is_available=_cloudops_available,
    extract_params=_extract_backend,
    injected_params=("cloudops_backend",),
)
def get_cluster_configuration(cloudops_backend: Any) -> dict[str, Any]:
    return _run_backend(cloudops_backend, "GetClusterConfiguration")


@tool(
    name="GetAlerts",
    source="eks",
    description=(
        "Get the active alerts that triggered this investigation. Call "
        "this FIRST in every case — the alert message identifies the "
        "affected service/namespace, severity, error rate, and timestamp. "
        "Don't reason from the alert headline alone; always pull the "
        "structured alert data."
    ),
    use_cases=[
        "Identify the affected service: alert tags name the failing component",
        "Establish when the issue started: alert firstSeen timestamp",
        "See the error pattern: alert message often contains HTTP 5xx, OOM, connection refused, etc.",
        "Determine severity: critical vs warning helps prioritize sub-investigations",
    ],
    requires=["cluster_name"],
    is_available=_cloudops_available,
    extract_params=_extract_backend,
    injected_params=("cloudops_backend",),
)
def get_alerts(cloudops_backend: Any) -> dict[str, Any]:
    return _run_backend(cloudops_backend, "GetAlerts")


@tool(
    name="GetErrorLogs",
    source="eks",
    description=(
        "Get aggregated error-log signals for a specific service: counts "
        "and example messages grouped by error type. Use AFTER finding a "
        "failing service from GetResources/events — error logs typically "
        "pinpoint the actual root cause (MySQL 'access denied', DNS "
        "'no such host', 'OOMKilled', 'image pull backoff', etc.)."
    ),
    use_cases=[
        "Confirm a suspected MySQL credential issue: look for 'Access denied for user' or '1045' MySQL error codes",
        "Confirm a DNS resolution failure: look for 'no such host' or 'DNS resolution failed'",
        "Identify image pull issues: 'ErrImagePull' or 'manifest unknown'",
        "Find HTTP 5xx patterns: 500/502/503/504 grouped by endpoint",
        "Detect connection-refused / port-mismatch errors against downstream services",
    ],
    requires=["cluster_name"],
    is_available=_cloudops_available,
    extract_params=_extract_error_logs,
    injected_params=("cloudops_backend",),
)
def get_error_logs(
    cloudops_backend: Any,
    namespace: str,
    service_name: str,
) -> dict[str, Any]:
    return _run_backend(
        cloudops_backend,
        "GetErrorLogs",
        namespace=namespace,
        service_name=service_name,
    )


@tool(
    name="GetRecentLogs",
    source="eks",
    description=(
        "Get the most recent log lines from a service — chronologically "
        "ordered, unfiltered. Use when GetErrorLogs aggregation isn't "
        "enough: recent logs show the SEQUENCE of events leading to "
        "failure, often revealing race conditions, startup ordering "
        "issues, or transient errors that don't surface in summaries."
    ),
    use_cases=[
        "See the moment of failure: tail logs around the alert timestamp",
        "Detect startup-sequence problems: container init order, secret/volume mount timing",
        "Find intermittent errors that aggregate-by-type misses",
        "Confirm a fix's effect by checking the latest log lines",
    ],
    requires=["cluster_name"],
    is_available=_cloudops_available,
    extract_params=_extract_recent_logs,
    injected_params=("cloudops_backend",),
)
def get_recent_logs(
    cloudops_backend: Any,
    namespace: str,
    service_name: str,
    lines: int = 50,
) -> dict[str, Any]:
    return _run_backend(
        cloudops_backend,
        "GetRecentLogs",
        namespace=namespace,
        service_name=service_name,
        lines=lines,
    )


@tool(
    name="GetServiceDependencies",
    source="eks",
    description=(
        "Map a service's upstream and downstream dependencies. Use this "
        "to trace cascading failures: if service A is failing, what "
        "calls A (impact blast-radius), and what does A call (potential "
        "root cause upstream)? Critical for distinguishing 'A is broken' "
        "from 'A is broken because B is broken'."
    ),
    use_cases=[
        "Trace the cause: which downstream services does the failing service depend on? Check those for errors too.",
        "Trace the impact: which upstream services call the failing one? Useful for confirming user-visible impact.",
        "Identify shared infrastructure: multiple failing services calling the same database/cache often points to that shared dep as the cause.",
    ],
    requires=["cluster_name"],
    is_available=_cloudops_available,
    extract_params=_extract_service_dependencies,
    injected_params=("cloudops_backend",),
)
def get_service_dependencies(cloudops_backend: Any, service_name: str) -> dict[str, Any]:
    return _run_backend(
        cloudops_backend,
        "GetServiceDependencies",
        service_name=service_name,
    )


@tool(
    name="GetAppYAML",
    source="eks",
    description=(
        "Get the full deployment YAML for an application — shows every "
        "secret reference, env var, volume mount, image tag, and resource "
        "limit. Use when DescribeResource doesn't show enough: the raw "
        "YAML often reveals misconfigured secret bindings, mismatched "
        "env-var names, wrong image tags, or sidecar/init-container "
        "issues that aren't obvious from the high-level describe."
    ),
    use_cases=[
        "Diagnose a missing secret binding: check 'envFrom' and 'volumes' sections for secret references",
        "Find image-tag mistakes: compare the spec's image vs the registered tag",
        "Detect resource-limit misconfigurations: CPU/memory requests and limits",
        "Check init-container ordering and sidecar configurations",
    ],
    requires=["cluster_name"],
    is_available=_cloudops_available,
    extract_params=_extract_app_yaml,
    injected_params=("cloudops_backend",),
)
def get_app_yaml(cloudops_backend: Any, app_name: str) -> dict[str, Any]:
    return _run_backend(cloudops_backend, "GetAppYAML", app_name=app_name)


@tool(
    name="CheckServiceConnectivity",
    source="eks",
    description=(
        "Test reachability of a Kubernetes service from inside the "
        "cluster. Use to confirm suspected service-routing failures: "
        "DNS resolution problems, port mismatches between Service and "
        "Pod, sidecar (Istio) port conflicts, or selector mismatches "
        "that leave a Service with zero endpoints."
    ),
    use_cases=[
        "Confirm DNS resolution failure: connectivity fails with 'no such host'",
        "Confirm port-mapping mismatch: connection refused on the Service port but pod listens on a different port",
        "Confirm zero-endpoint failures: 'no endpoints available'",
        "Validate that a fix resolved the connectivity issue",
    ],
    requires=["cluster_name"],
    is_available=_cloudops_available,
    extract_params=_extract_connectivity,
    injected_params=("cloudops_backend",),
)
def check_service_connectivity(
    cloudops_backend: Any,
    service_name: str,
    port: int,
    namespace: str,
) -> dict[str, Any]:
    return _run_backend(
        cloudops_backend,
        "CheckServiceConnectivity",
        service_name=service_name,
        port=port,
        namespace=namespace,
    )


@tool(
    name="CheckNodeServiceStatus",
    source="eks",
    description=(
        "Check the health of a specific Kubernetes control-plane "
        "component (kubelet, kube-scheduler, kube-proxy, containerd) "
        "on a named node. Use when GetClusterConfiguration reveals "
        "node-level issues OR when GetResources shows scheduling "
        "failures, Pending pods, or NotReady nodes."
    ),
    use_cases=[
        "Diagnose scheduling failures: check kube-scheduler on master nodes",
        "Diagnose pod-startup failures: check kubelet on the worker node",
        "Diagnose container-runtime issues: check containerd on the affected node",
        "Diagnose service-routing failures: check kube-proxy on relevant nodes",
    ],
    requires=["cluster_name"],
    is_available=_cloudops_available,
    extract_params=_extract_node_status,
    injected_params=("cloudops_backend",),
)
def check_node_service_status(
    cloudops_backend: Any,
    node_name: str,
    service_name: str,
) -> dict[str, Any]:
    return _run_backend(
        cloudops_backend,
        "CheckNodeServiceStatus",
        node_name=node_name,
        service_name=service_name,
    )
