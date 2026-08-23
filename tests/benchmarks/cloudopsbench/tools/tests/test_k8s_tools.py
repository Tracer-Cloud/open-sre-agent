from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.benchmarks.cloudopsbench.tools import k8s as k8s_tools
from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence


class _Backend:
    default_namespace = "boutique"

    def __init__(self, process: dict[str, list[str]]) -> None:
        self.case = SimpleNamespace(
            process=process, result=SimpleNamespace(fault_object="app/cartservice")
        )


def _tool_params(tool_func: Any, sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return tool_func.__opensre_registered_tool__.extract_params(sources)


def test_recent_logs_extracts_its_own_service_name() -> None:
    backend = _Backend(
        {
            "path1": [
                "GetErrorLogs::frontend",
                "GetRecentLogs::cartservice",
            ],
            "path2": [],
        }
    )
    # Bench backend lives in its dedicated slot (_bench_backend), distinct
    # from the synthetic-test ``_backend`` slot. This is what the
    # slot-separation refactor enforces.
    sources = {"eks": {"_bench_backend": backend, "namespace": "boutique"}}

    error_params = _tool_params(k8s_tools.get_error_logs, sources)
    recent_params = _tool_params(k8s_tools.get_recent_logs, sources)

    assert error_params["service_name"] == "frontend"
    assert recent_params["service_name"] == "cartservice"
    assert recent_params["namespace"] == "boutique"


@pytest.mark.parametrize(
    ("tool_func", "tool_input", "rendered_output", "expected_summary"),
    [
        (
            k8s_tools.get_resources,
            {"resource_type": "pods", "namespace": "boutique"},
            [{"name": "frontend-abc123"}],
            "pods in boutique",
        ),
        (
            k8s_tools.describe_resource,
            {"resource_type": "deployment", "name": "frontend", "namespace": "boutique"},
            {"kind": "Deployment", "name": "frontend"},
            "deployment frontend in boutique",
        ),
        (
            k8s_tools.get_cluster_configuration,
            {},
            {"nodes": ["node-a"]},
            "cluster configuration snapshot",
        ),
        (
            k8s_tools.get_alerts,
            {},
            "2 active alerts",
            "active alerts snapshot",
        ),
        (
            k8s_tools.get_error_logs,
            {"namespace": "boutique", "service_name": "frontend"},
            "Access denied for user",
            "frontend error logs",
        ),
        (
            k8s_tools.get_recent_logs,
            {"namespace": "boutique", "service_name": "cartservice"},
            ["recent line 1", "recent line 2"],
            "cartservice recent logs",
        ),
        (
            k8s_tools.get_service_dependencies,
            {"service_name": "frontend"},
            {"downstream": ["catalogservice"]},
            "frontend dependencies",
        ),
        (
            k8s_tools.get_app_yaml,
            {"app_name": "frontend"},
            "apiVersion: apps/v1",
            "frontend deployment YAML",
        ),
        (
            k8s_tools.check_service_connectivity,
            {"service_name": "frontend", "port": 80, "namespace": "boutique"},
            "Connection OK",
            "frontend:80 connectivity",
        ),
        (
            k8s_tools.check_node_service_status,
            {"node_name": "node-a", "service_name": "kubelet"},
            {"status": "Running"},
            "node-a kubelet status",
        ),
    ],
)
def test_cloudopsbench_k8s_tools_record_citeable_evidence(
    tool_func: Any,
    tool_input: dict[str, Any],
    rendered_output: Any,
    expected_summary: str,
) -> None:
    evidence: dict[str, Any] = {}
    tool_name = tool_func.__opensre_registered_tool__.name
    output = {
        "source": "cloudopsbench",
        "available": True,
        "action_name": tool_name,
        "action_input": tool_input,
        "output": rendered_output,
        "cache_key": "cache-key",
        "cache_hit": True,
    }

    merge_tool_evidence(evidence, tool_name, output, tool_input)

    assert evidence[tool_name] == output
    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    entry = next(item for item in entries if item["source"] == tool_name)
    assert expected_summary in (entry.get("summary") or "")
    assert entry.get("label")
