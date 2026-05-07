from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.tools.CloudOpsBenchK8sTools import get_error_logs, get_recent_logs


class _Backend:
    is_cloudopsbench_backend = True
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
    sources = {"eks": {"_backend": backend, "namespace": "boutique"}}

    error_params = _tool_params(get_error_logs, sources)
    recent_params = _tool_params(get_recent_logs, sources)

    assert error_params["service_name"] == "frontend"
    assert recent_params["service_name"] == "cartservice"
    assert recent_params["namespace"] == "boutique"
