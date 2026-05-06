from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.cloudopsbench.case_loader import CloudOpsCase, read_json_object

RESOURCE_ALIASES: dict[str, str] = {
    "pod": "pods",
    "pods": "pods",
    "po": "pods",
    "service": "services",
    "services": "services",
    "svc": "services",
    "deployment": "deployments",
    "deployments": "deployments",
    "deploy": "deployments",
    "statefulset": "statefulsets",
    "statefulsets": "statefulsets",
    "sts": "statefulsets",
    "daemonset": "daemonsets",
    "daemonsets": "daemonsets",
    "ds": "daemonsets",
    "configmap": "configmaps",
    "configmaps": "configmaps",
    "cm": "configmaps",
    "secret": "secrets",
    "secrets": "secrets",
    "persistentvolumeclaim": "persistentvolumeclaims",
    "persistentvolumeclaims": "persistentvolumeclaims",
    "pvc": "persistentvolumeclaims",
    "replicaset": "replicasets",
    "replicasets": "replicasets",
    "rs": "replicasets",
    "ingress": "ingresses",
    "ingresses": "ingresses",
    "ing": "ingresses",
    "networkpolicy": "networkpolicies",
    "networkpolicies": "networkpolicies",
    "netpol": "networkpolicies",
    "serviceaccount": "serviceaccounts",
    "serviceaccounts": "serviceaccounts",
    "sa": "serviceaccounts",
    "job": "jobs",
    "jobs": "jobs",
    "endpoint": "endpoints",
    "endpoints": "endpoints",
    "ep": "endpoints",
    "persistentvolume": "persistentvolumes",
    "persistentvolumes": "persistentvolumes",
    "pv": "persistentvolumes",
    "namespace": "namespaces",
    "namespaces": "namespaces",
    "ns": "namespaces",
    "node": "nodes",
    "nodes": "nodes",
    "no": "nodes",
    "storageclass": "storageclasses",
    "storageclasses": "storageclasses",
    "sc": "storageclasses",
    "event": "events",
    "events": "events",
    "resourcequota": "resourcequota",
    "resourcequotas": "resourcequota",
}

CLUSTER_SCOPED_RESOURCE_TYPES = {
    "nodes",
    "persistentvolumes",
    "storageclasses",
    "namespaces",
}


@dataclass(frozen=True)
class CloudOpsToolResult:
    action_name: str
    action_input: dict[str, Any]
    output: Any
    cache_key: str
    cache_hit: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": "cloudopsbench",
            "available": True,
            "action_name": self.action_name,
            "action_input": dict(self.action_input),
            "output": self.output,
            "cache_key": self.cache_key,
            "cache_hit": self.cache_hit,
        }


def normalize_resource_type(resource_type: str | None) -> str:
    if not resource_type:
        return ""
    return RESOURCE_ALIASES.get(resource_type.strip().lower(), "")


def build_cache_key(tool_name: str, params: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(params, ensure_ascii=False, separators=(',', ':'))}"


class CloudOpsBenchReplayBackend:
    """Replay Cloud-OpsBench tool outputs from the official per-case cache."""

    is_cloudopsbench_backend = True

    def __init__(self, case: CloudOpsCase) -> None:
        self.case = case
        self.default_namespace = case.namespace
        self.tool_cache = read_json_object(case.tool_cache_path)
        self.raw_logs = self._read_raw_logs(case.case_dir)
        self.action_log: list[dict[str, Any]] = []

    @staticmethod
    def _read_raw_logs(case_dir: Path) -> dict[str, Any]:
        path = case_dir / "raw_data" / "logs.json"
        if not path.is_file():
            return {}
        return read_json_object(path)

    def _lookup(self, candidate_keys: list[str]) -> tuple[Any, str, bool]:
        for key in candidate_keys:
            if key in self.tool_cache:
                return self.tool_cache[key], key, True
        return "", candidate_keys[0] if candidate_keys else "", False

    def _record(self, result: CloudOpsToolResult) -> dict[str, Any]:
        payload = result.to_dict()
        self.action_log.append(payload)
        return payload

    def _cluster_scope_namespaces(self, namespace: str | None) -> list[str | None]:
        candidates: list[str | None] = [None, ""]
        if namespace:
            candidates.append(namespace)
        candidates.append(self.default_namespace)

        ordered: list[str | None] = []
        for item in candidates:
            if item not in ordered:
                ordered.append(item)
        return ordered

    def _get_resources_candidate_keys(
        self,
        resource_type_norm: str,
        namespace: str | None,
        name: str | None,
        *,
        show_labels: bool = False,
        output_wide: bool = False,
        label_selector: str | None = None,
    ) -> list[str]:
        namespaces = (
            self._cluster_scope_namespaces(namespace)
            if resource_type_norm in CLUSTER_SCOPED_RESOURCE_TYPES
            else [namespace]
        )
        keys: list[str] = []
        for ns in namespaces:
            params: dict[str, Any] = {
                "resource_type": resource_type_norm,
                "name": name if name is not None else "",
            }
            if ns is not None:
                params["namespace"] = ns
            if output_wide:
                params["output_wide"] = True
            if show_labels:
                params["show_labels"] = True
            if label_selector:
                params["label_selector"] = label_selector
            keys.append(build_cache_key("GetResources", params))
        return keys

    def _describe_candidate_keys(
        self,
        resource_type_norm: str,
        name: str,
        namespace: str | None,
    ) -> list[str]:
        namespaces = (
            self._cluster_scope_namespaces(namespace)
            if resource_type_norm in CLUSTER_SCOPED_RESOURCE_TYPES
            else [namespace]
        )
        keys: list[str] = []
        for ns in namespaces:
            params: dict[str, Any] = {
                "resource_type": resource_type_norm,
                "name": name,
            }
            if ns is not None:
                params["namespace"] = ns
            keys.append(build_cache_key("DescribeResource", params))
        return keys

    def GetResources(
        self,
        resource_type: str,
        namespace: str | None = None,
        name: str | None = None,
        show_labels: bool = False,
        output_wide: bool = False,
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        resource_type_norm = normalize_resource_type(resource_type)
        if not resource_type_norm:
            return self._error("GetResources", {}, f"Unknown resource type: {resource_type!r}")

        is_cluster_scoped = resource_type_norm in CLUSTER_SCOPED_RESOURCE_TYPES
        resolved_namespace = None if is_cluster_scoped else namespace or self.default_namespace
        action_input = {
            "resource_type": resource_type_norm,
            "namespace": resolved_namespace,
            "name": name,
            "show_labels": show_labels,
            "output_wide": output_wide,
            "label_selector": label_selector,
        }
        value, key, hit = self._lookup(
            self._get_resources_candidate_keys(
                resource_type_norm=resource_type_norm,
                namespace=resolved_namespace,
                name=name,
                show_labels=show_labels,
                output_wide=output_wide,
                label_selector=label_selector,
            )
        )
        if not hit:
            value = f"Error: cache entry not found for {key}"
        return self._record(CloudOpsToolResult("GetResources", action_input, value, key, hit))

    def DescribeResource(
        self,
        resource_type: str,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        resource_type_norm = normalize_resource_type(resource_type)
        if not resource_type_norm:
            return self._error("DescribeResource", {}, f"Unknown resource type: {resource_type!r}")
        if not name:
            return self._error("DescribeResource", {}, "name is required")

        is_cluster_scoped = resource_type_norm in CLUSTER_SCOPED_RESOURCE_TYPES
        resolved_namespace = None if is_cluster_scoped else namespace or self.default_namespace
        action_input = {
            "resource_type": resource_type_norm,
            "name": name,
            "namespace": resolved_namespace,
        }
        value, key, hit = self._lookup(
            self._describe_candidate_keys(resource_type_norm, name, resolved_namespace)
        )
        if not hit:
            value = f"Error: cache entry not found for {key}"
        return self._record(CloudOpsToolResult("DescribeResource", action_input, value, key, hit))

    def GetClusterConfiguration(self) -> dict[str, Any]:
        value, key, hit = self._lookup(["GetClusterConfiguration:{}", "GetClusterConfiguration"])
        if not hit:
            value = "Error: Cluster configuration snapshot is not available in the dataset."
        return self._record(CloudOpsToolResult("GetClusterConfiguration", {}, value, key, hit))

    def GetAlerts(self) -> dict[str, Any]:
        key = "GetAlerts:{}"
        value, _, hit = self._lookup([key])
        if not hit:
            value = "Error: Cluster alerts is not available in the dataset."
        elif value in ("", None, [], {}):
            value = "No active metric anomalies detected at this time."
        return self._record(CloudOpsToolResult("GetAlerts", {}, value, key, hit))

    def GetErrorLogs(self, namespace: str, service_name: str) -> dict[str, Any]:
        params = {"namespace": namespace or self.default_namespace, "service_name": service_name}
        key = build_cache_key("GetErrorLogs", params)
        value, _, hit = self._lookup([key])
        if not hit:
            value = "Error: Error logs for the specified service are not available."
        elif value in ("", None, [], {}):
            value = f"No error logs found for {service_name} in {params['namespace']} namespace."
        elif isinstance(value, dict):
            if value.get("total_errors") == 0:
                value = (
                    f"No error logs found for {service_name} in {params['namespace']} namespace."
                )
            else:
                value = json.dumps(value, indent=2, ensure_ascii=False)
        return self._record(CloudOpsToolResult("GetErrorLogs", params, value, key, hit))

    def GetRecentLogs(
        self,
        namespace: str,
        service_name: str,
        lines: int = 50,
    ) -> dict[str, Any]:
        entries = self._collect_service_log_entries(service_name, lines)
        output: Any
        if entries:
            output = entries
        else:
            output = f"No recent logs found for {service_name} in {namespace} namespace."
        params = {
            "namespace": namespace or self.default_namespace,
            "service_name": service_name,
            "lines": lines,
        }
        return self._record(CloudOpsToolResult("GetRecentLogs", params, output, "", bool(entries)))

    def GetServiceDependencies(self, service_name: str) -> dict[str, Any]:
        params = {"service_name": service_name}
        key = build_cache_key("GetServiceDependencies", params)
        value, _, hit = self._lookup([key])
        if not hit:
            value = f"Error: Dependencies for {service_name!r} not recorded in trace data."
        return self._record(CloudOpsToolResult("GetServiceDependencies", params, value, key, hit))

    def GetAppYAML(self, app_name: str) -> dict[str, Any]:
        params = {"app_name": app_name}
        key = build_cache_key("GetAppYAML", params)
        value, _, hit = self._lookup([key])
        if not hit:
            value = f"Error: YAML configuration for {app_name!r} is not recorded."
        return self._record(CloudOpsToolResult("GetAppYAML", params, value, key, hit))

    def CheckServiceConnectivity(
        self,
        service_name: str,
        port: int,
        namespace: str,
    ) -> dict[str, Any]:
        params = {
            "namespace": namespace or self.default_namespace,
            "service_name": service_name,
            "port": int(port),
        }
        key = build_cache_key("CheckServiceConnectivity", params)
        value, _, hit = self._lookup([key])
        if not hit:
            value = "Connection failed"
        return self._record(CloudOpsToolResult("CheckServiceConnectivity", params, value, key, hit))

    def CheckNodeServiceStatus(self, node_name: str, service_name: str) -> dict[str, Any]:
        params = {"node_name": node_name, "service_name": service_name}
        key = build_cache_key("CheckNodeServiceStatus", params)
        value, _, hit = self._lookup([key])
        if not hit:
            value = "Error: Status information for cluster control plane components is unavailable."
        return self._record(CloudOpsToolResult("CheckNodeServiceStatus", params, value, key, hit))

    def list_pods(self, cluster_name: str = "", namespace: str = "", **_: Any) -> dict[str, Any]:
        result = self.GetResources("pods", namespace=namespace or self.default_namespace)
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace or self.default_namespace,
            "pods": [],
            "failing_pods": [],
            "high_restart_pods": [],
            "total_pods": 0,
            "raw_output": result.get("output"),
            "error": None,
        }

    def get_events(self, cluster_name: str = "", namespace: str = "", **_: Any) -> dict[str, Any]:
        result = self.GetResources("events", namespace=namespace or self.default_namespace)
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace or self.default_namespace,
            "warning_events": [],
            "total_warning_count": 0,
            "raw_output": result.get("output"),
            "error": None,
        }

    def list_deployments(
        self, cluster_name: str = "", namespace: str = "", **_: Any
    ) -> dict[str, Any]:
        result = self.GetResources("deployments", namespace=namespace or self.default_namespace)
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace or self.default_namespace,
            "deployments": [],
            "degraded_deployments": [],
            "total_deployments": 0,
            "raw_output": result.get("output"),
            "error": None,
        }

    def get_node_health(self, cluster_name: str = "", **_: Any) -> dict[str, Any]:
        result = self.GetResources("nodes")
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "nodes": [],
            "total_nodes": 0,
            "not_ready_count": 0,
            "raw_output": result.get("output"),
            "error": None,
        }

    def get_pod_logs(
        self,
        cluster_name: str = "",
        namespace: str = "",
        pod_name: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        service_name = pod_name.split("-", 1)[0] if pod_name else ""
        result = self.GetRecentLogs(namespace or self.default_namespace, service_name)
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace or self.default_namespace,
            "pod_name": pod_name,
            "logs": result.get("output"),
            "error": None,
        }

    def _collect_service_log_entries(self, service_name: str, lines: int) -> list[str]:
        service_sources = [
            (service_name, f"From {service_name} logs:"),
            (f"{service_name}.previous", "From previous container logs:"),
            (f"{service_name}.istio-proxy", "From istio-proxy logs:"),
        ]
        collected: list[str] = []
        for key, header in service_sources:
            raw_value = self.raw_logs.get(key)
            entries = self._normalize_log_entries(raw_value)
            if not entries:
                continue
            if collected:
                collected.append("")
            collected.append(header)
            collected.extend(entries[-lines:])
        return collected

    @staticmethod
    def _normalize_log_entries(raw_value: Any) -> list[str]:
        if raw_value in (None, "", []):
            return []
        if isinstance(raw_value, list):
            return [str(item) for item in raw_value if str(item).strip()]
        if isinstance(raw_value, str):
            return [line for line in raw_value.splitlines() if line.strip()]
        return [str(raw_value)]

    def _error(self, action_name: str, action_input: dict[str, Any], error: str) -> dict[str, Any]:
        payload = {
            "source": "cloudopsbench",
            "available": False,
            "action_name": action_name,
            "action_input": action_input,
            "output": "",
            "cache_key": "",
            "cache_hit": False,
            "error": error,
        }
        self.action_log.append(payload)
        return payload
