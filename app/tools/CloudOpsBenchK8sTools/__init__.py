"""Cloud-OpsBench cache-backed Kubernetes tools.

Replays Cloud-OpsBench (Wang et al., arXiv:2603.00468) actions against the
per-case ``tool_cache.json`` instead of talking to a real EKS cluster.
The tools are gated on ``is_cloudopsbench_backend`` so they only appear
to the LLM in replay mode; the real EKS tools take over otherwise.

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

    Helpers in this module duck-type via
    ``getattr(backend, "is_cloudopsbench_backend", False)`` rather than
    ``isinstance(backend, _CloudOpsBenchBackend)`` — this Protocol exists
    for human readers and IDE navigation, not for runtime enforcement.
    Any object exposing the marker attribute participates.
    """

    # Marker attribute. ``True`` on the replay backend; absent (and so
    # treated as ``False`` via ``getattr`` default) on real EKS sources.
    is_cloudopsbench_backend: bool

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
    backend = (sources.get("eks") or {}).get("_backend")
    if getattr(backend, "is_cloudopsbench_backend", False):
        return backend
    return None


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
    description="Replay Cloud-OpsBench GetResources against the case tool_cache.json.",
    use_cases=["List Kubernetes resources recorded in the benchmark snapshot."],
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
    description="Replay Cloud-OpsBench DescribeResource against the case tool_cache.json.",
    use_cases=["Inspect details for a recorded Kubernetes resource."],
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
    description="Replay Cloud-OpsBench GetClusterConfiguration from tool_cache.json.",
    use_cases=["Inspect recorded cluster-level configuration."],
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
    description="Replay Cloud-OpsBench GetAlerts from tool_cache.json.",
    use_cases=["Inspect recorded metric alerts for the case."],
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
    description="Replay Cloud-OpsBench GetErrorLogs for the benchmark case.",
    use_cases=["Inspect service error-log summaries recorded in the snapshot."],
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
    description="Replay Cloud-OpsBench GetRecentLogs for the benchmark case.",
    use_cases=["Inspect recent service logs recorded in raw_data/logs.json."],
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
    description="Replay Cloud-OpsBench GetServiceDependencies from tool_cache.json.",
    use_cases=["Inspect recorded service dependency topology."],
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
    description="Replay Cloud-OpsBench GetAppYAML from tool_cache.json.",
    use_cases=["Inspect recorded YAML for an application service."],
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
    description="Replay Cloud-OpsBench CheckServiceConnectivity from tool_cache.json.",
    use_cases=["Check recorded service connectivity result."],
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
    description="Replay Cloud-OpsBench CheckNodeServiceStatus from tool_cache.json.",
    use_cases=["Check recorded node component status."],
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
