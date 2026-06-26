from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tests.benchmarks.cloudopsbench.tools.k8s import get_error_logs, get_recent_logs


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

    error_params = _tool_params(get_error_logs, sources)
    recent_params = _tool_params(get_recent_logs, sources)

    assert error_params["service_name"] == "frontend"
    assert recent_params["service_name"] == "cartservice"
    assert recent_params["namespace"] == "boutique"
