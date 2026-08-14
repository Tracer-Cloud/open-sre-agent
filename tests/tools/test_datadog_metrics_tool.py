"""Tests for DataDogMetricsTool (function-based stub, @tool decorated)."""

from __future__ import annotations

from typing import Any

import integrations.datadog.tools as datadog_tools
from integrations.datadog.tools import query_datadog_metrics
from tests.tools.conftest import BaseToolContract, mock_agent_state


class _FakeDatadogBackend:
    def query_metrics(
        self,
        *,
        metric_name: str,
        time_range_minutes: int,
        query: str | None,
    ) -> dict[str, Any]:
        return {
            "source": "fixture_datadog_metrics",
            "available": True,
            "metric_name": metric_name,
            "time_range_minutes": time_range_minutes,
            "query": query,
            "metrics": [{"scope": "host:fixture", "points": []}],
        }


class TestDataDogMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_datadog_metrics.__opensre_registered_tool__


def test_is_available_when_connected_or_backend_injected() -> None:
    rt = query_datadog_metrics.__opensre_registered_tool__
    assert rt.is_available({"datadog": {"connection_verified": True}}) is True
    assert rt.is_available({"datadog": {"_backend": _FakeDatadogBackend()}}) is True
    assert rt.is_available({"datadog": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_datadog_metrics.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert "metric_name" in params
    assert params["api_key"] == "dd_api_key_test"


def test_run_returns_unavailable_without_credentials() -> None:
    result = query_datadog_metrics(metric_name="system.cpu.user")
    assert result["available"] is False
    assert result["metric_name"] == "system.cpu.user"
    assert result["metrics"] == []


def test_run_queries_all_metric_series(monkeypatch) -> None:
    class _FakeClient:
        def query_metrics(self, query: str, *, start, end) -> dict[str, Any]:
            assert query == "avg:system.cpu.user{*}"
            assert start < end
            return {
                "success": True,
                "timestamps": ["2023-11-14T22:13:20Z"],
                "values": [42.5],
                "series": [
                    {
                        "metric": "system.cpu.user",
                        "scope": "host:web-1",
                        "expression": "avg:system.cpu.user{*} by {host}",
                        "points": [{"timestamp": "2023-11-14T22:13:20Z", "value": 42.5}],
                    },
                    {
                        "metric": "system.cpu.user",
                        "scope": "host:web-2",
                        "expression": "avg:system.cpu.user{*} by {host}",
                        "points": [{"timestamp": "2023-11-14T22:13:20Z", "value": 17.0}],
                    },
                ],
            }

    def _make_client(_api_key: str | None, _app_key: str | None, _site: str) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr(datadog_tools, "make_client", _make_client)

    result = query_datadog_metrics(
        metric_name="system.cpu.user",
        api_key="api-key",
        app_key="app-key",
    )

    assert result["available"] is True
    assert result["metric_name"] == "system.cpu.user"
    assert len(result["metrics"]) == 2
    assert result["metrics"][1]["scope"] == "host:web-2"


def test_run_preserves_explicit_query(monkeypatch) -> None:
    class _FakeClient:
        def query_metrics(self, query: str, *, start, end) -> dict[str, Any]:
            assert query == "max:system.cpu.user{env:prod} by {host}"
            assert start < end
            return {"success": True, "timestamps": [], "values": [], "series": []}

    def _make_client(_api_key: str | None, _app_key: str | None, _site: str) -> _FakeClient:
        return _FakeClient()

    monkeypatch.setattr(datadog_tools, "make_client", _make_client)

    result = query_datadog_metrics(
        metric_name="system.cpu.user",
        query="max:system.cpu.user{env:prod} by {host}",
        api_key="api-key",
        app_key="app-key",
    )

    assert result["available"] is True
    assert result["metrics"] == []


def test_run_delegates_to_fixture_backend() -> None:
    result = query_datadog_metrics(
        metric_name="system.cpu.user",
        time_range_minutes=15,
        query="avg:system.cpu.user{service:checkout}",
        datadog_backend=_FakeDatadogBackend(),
    )

    assert result["source"] == "fixture_datadog_metrics"
    assert result["time_range_minutes"] == 15


def test_run_returns_client_error_as_unavailable(monkeypatch) -> None:
    class _FailingClient:
        def query_metrics(self, query: str, *, start, end) -> dict[str, Any]:
            assert query == "avg:system.cpu.user{*}"
            assert start < end
            return {"success": False, "error": "Datadog rate limit exceeded"}

    def _make_client(_api_key: str | None, _app_key: str | None, _site: str) -> _FailingClient:
        return _FailingClient()

    monkeypatch.setattr(datadog_tools, "make_client", _make_client)

    result = query_datadog_metrics(
        metric_name="system.cpu.user",
        api_key="api-key",
        app_key="app-key",
    )

    assert result["available"] is False
    assert result["metrics"] == []
    assert result["error"] == "Datadog rate limit exceeded"


def test_run_metadata() -> None:
    rt = query_datadog_metrics.__opensre_registered_tool__
    assert rt.name == "query_datadog_metrics"
    assert rt.source == "datadog"
