"""Cloud-OpsBench cache-backed Kubernetes tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from app.tools.tool_decorator import tool


class _CloudOpsBenchBackend(Protocol):
    is_cloudopsbench_backend: bool
    case: Any
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
    for path_name in ("path1", "path2"):
        for step in process.get(path_name, []):
            if not isinstance(step, str):
                continue
            parts = step.split("::")
            if len(parts) >= 2 and parts[0] in {
                "GetErrorLogs",
                "GetRecentLogs",
                "GetServiceDependencies",
                "GetAppYAML",
            }:
                return parts[1]

    result = getattr(case, "result", None)
    fault_object = getattr(result, "fault_object", "")
    if isinstance(fault_object, str) and fault_object.startswith("app/"):
        return fault_object.split("/", 1)[1]
    return "frontend"


def _process_parts_for_action(backend: Any, action_name: str) -> list[str]:
    case = getattr(backend, "case", None)
    process = getattr(case, "process", {}) or {}
    for path_name in ("path2", "path1"):
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
    return "pods"


def _default_namespace(backend: Any, sources: dict[str, dict]) -> str:
    eks = sources.get("eks") or {}
    namespace = eks.get("namespace") or getattr(backend, "default_namespace", "")
    return str(namespace or "default")


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
    resource_type = parts[1] if len(parts) >= 2 else "services"
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
        "port": int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 80,
        "namespace": _default_namespace(backend, sources),
    }


def _extract_node_status(sources: dict[str, dict]) -> dict[str, Any]:
    backend = _cloudops_backend(sources)
    parts = _process_parts_for_action(backend, "CheckNodeServiceStatus")
    return {
        "cloudops_backend": backend,
        "node_name": parts[1] if len(parts) >= 2 else "master",
        "service_name": parts[2] if len(parts) >= 3 else "kube-scheduler",
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
