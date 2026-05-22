"""Tests for DataDogMetricsTool (function-based, @tool decorated)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.tools.DataDogMetricsTool import query_datadog_metrics
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestDataDogMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_datadog_metrics.__opensre_registered_tool__


class _MetricsBackend:
    def query_metrics(self, **_kwargs: object) -> dict[str, object]:
        return {"source": "datadog_metrics", "available": True, "metrics": []}


def test_is_available_requires_connection_or_metrics_backend() -> None:
    rt = query_datadog_metrics.__opensre_registered_tool__
    assert rt.is_available({"datadog": {"connection_verified": True}}) is True
    assert rt.is_available({"datadog": {"_backend": _MetricsBackend()}}) is True
    assert rt.is_available({"datadog": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_datadog_metrics.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert "metric_name" in params
    assert params["api_key"] == "dd_api_key_test"
    assert params["app_key"] == "dd_app_key_test"
    assert params["datadog_backend"] is None


def test_run_returns_unavailable_when_no_client() -> None:
    result = query_datadog_metrics(metric_name="system.cpu.user", api_key=None, app_key=None)
    assert result["available"] is False
    assert result["metric_name"] == "system.cpu.user"
    assert result["query"] == "avg:system.cpu.user{*}"
    assert result["metrics"] == []


def test_run_requires_metric_or_query() -> None:
    result = query_datadog_metrics(metric_name="", query=" ")
    assert result["available"] is False
    assert "metric name or query" in result["error"]


def test_run_rejects_invalid_time_range() -> None:
    result = query_datadog_metrics(metric_name="system.cpu.user", time_range_minutes=0)
    assert result["available"] is False
    assert "time_range_minutes" in result["error"]


def test_run_happy_path_queries_client_and_summarizes_series() -> None:
    mock_client = MagicMock()
    mock_client.query_metrics.return_value = {
        "success": True,
        "total_series": 1,
        "series": [
            {
                "metric": "system.cpu.user",
                "scope": "host:web-01",
                "tags": ["host:web-01"],
                "unit": [{"family": "percentage"}],
                "point_count": 3,
                "points": [
                    {"timestamp": "2026-05-22T10:00:00Z", "value": 10.0},
                    {"timestamp": "2026-05-22T10:01:00Z", "value": 20.0},
                    {"timestamp": "2026-05-22T10:02:00Z", "value": 40.0},
                ],
                "values": [10.0, 20.0, 40.0],
            }
        ],
    }

    with patch("app.tools.DataDogMetricsTool.make_client", return_value=mock_client):
        result = query_datadog_metrics(
            metric_name="system.cpu.user",
            time_range_minutes=15,
            api_key="key",
            app_key="app",
        )

    assert result["available"] is True
    assert result["query"] == "avg:system.cpu.user{*}"
    assert result["total_series"] == 1
    assert result["metrics"][0]["metric_name"] == "system.cpu.user"
    assert result["metrics"][0]["summary"] == {
        "first": 10.0,
        "latest": 40.0,
        "min": 10.0,
        "max": 40.0,
        "avg": 23.3333,
        "delta": 30.0,
        "trend": "increased",
        "delta_pct": 300.0,
    }

    _, kwargs = mock_client.query_metrics.call_args
    assert kwargs["start"].tzinfo == UTC
    assert kwargs["end"].tzinfo == UTC
    assert isinstance(kwargs["start"], datetime)
    assert mock_client.query_metrics.call_args.args == ("avg:system.cpu.user{*}",)


def test_run_adds_wildcard_scope_to_aggregation_prefixed_metric() -> None:
    mock_client = MagicMock()
    mock_client.query_metrics.return_value = {"success": True, "series": [], "total_series": 0}

    with patch("app.tools.DataDogMetricsTool.make_client", return_value=mock_client):
        result = query_datadog_metrics(
            metric_name="avg:system.cpu.user",
            api_key="key",
            app_key="app",
        )

    assert result["available"] is True
    assert result["query"] == "avg:system.cpu.user{*}"
    assert mock_client.query_metrics.call_args.args == ("avg:system.cpu.user{*}",)


def test_run_summary_includes_null_delta_pct_when_first_value_is_zero() -> None:
    mock_client = MagicMock()
    mock_client.query_metrics.return_value = {
        "success": True,
        "total_series": 1,
        "series": [
            {
                "metric": "custom.zero_start",
                "point_count": 2,
                "values": [0.0, 5.0],
            }
        ],
    }

    with patch("app.tools.DataDogMetricsTool.make_client", return_value=mock_client):
        result = query_datadog_metrics(
            metric_name="custom.zero_start",
            api_key="key",
            app_key="app",
        )

    assert result["available"] is True
    assert result["metrics"][0]["summary"]["delta_pct"] is None


def test_run_preserves_full_query_override() -> None:
    mock_client = MagicMock()
    mock_client.query_metrics.return_value = {"success": True, "series": [], "total_series": 0}

    with patch("app.tools.DataDogMetricsTool.make_client", return_value=mock_client):
        result = query_datadog_metrics(
            metric_name="ignored.metric",
            query="sum:custom.errors{service:api}.as_count()",
            api_key="key",
            app_key="app",
        )

    assert result["available"] is True
    assert result["query"] == "sum:custom.errors{service:api}.as_count()"
    assert mock_client.query_metrics.call_args.args == (
        "sum:custom.errors{service:api}.as_count()",
    )


def test_run_returns_backend_result_when_available() -> None:
    backend = MagicMock()
    backend.query_metrics.return_value = {
        "source": "datadog_metrics",
        "available": True,
        "metrics": [{"metric_name": "custom.metric"}],
    }

    result = query_datadog_metrics(
        metric_name="custom.metric",
        datadog_backend=backend,
    )

    assert result["available"] is True
    backend.query_metrics.assert_called_once_with(
        metric_name="custom.metric",
        query="avg:custom.metric{*}",
        time_range_minutes=60,
    )


def test_run_api_error_returns_unavailable() -> None:
    mock_client = MagicMock()
    mock_client.query_metrics.return_value = {"success": False, "error": "forbidden"}

    with patch("app.tools.DataDogMetricsTool.make_client", return_value=mock_client):
        result = query_datadog_metrics(
            metric_name="system.cpu.user",
            api_key="key",
            app_key="app",
        )

    assert result["available"] is False
    assert result["error"] == "forbidden"


def test_run_metadata() -> None:
    rt = query_datadog_metrics.__opensre_registered_tool__
    assert rt.name == "query_datadog_metrics"
    assert rt.source == "datadog"
