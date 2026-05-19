"""Tests for SigNoz tools."""

from typing import Any

from app.tools.SignozLogsTool import query_signoz_logs
from app.tools.SignozMetricsTool import query_signoz_metrics
from app.tools.SignozTracesTool import query_signoz_traces


class _FakeSigNozBackend:
    """Fake backend for synthetic tests."""

    def query_logs(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_logs",
            "available": True,
            "total": 2,
            "logs": [
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "severity": "ERROR",
                    "severity_number": 17,
                    "message": "connection refused",
                    "trace_id": "abc123",
                    "span_id": "def456",
                    "attributes": {"http.method": "GET"},
                    "resources": {"service.name": "api"},
                },
                {
                    "timestamp": "2024-01-01T00:01:00Z",
                    "severity": "INFO",
                    "severity_number": 9,
                    "message": "request completed",
                    "trace_id": "",
                    "span_id": "",
                    "attributes": {},
                    "resources": {"service.name": "api"},
                },
            ],
        }

    def query_metrics(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_metrics",
            "available": True,
            "total": 2,
            "metric_name": kwargs.get("metric_name"),
            "resolved_metric": kwargs.get("metric_name"),
            "aggregation": kwargs.get("aggregation"),
            "metrics": [
                {
                    "interval": "2024-01-01 00:00:00",
                    "value": 42.0,
                    "metric_name": kwargs.get("metric_name"),
                    "service_name": kwargs.get("service") or "",
                },
            ],
        }

    def query_traces(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_traces",
            "available": True,
            "total": 1,
            "traces": [
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "trace_id": "abc123",
                    "span_id": "span1",
                    "name": "GET /api/health",
                    "duration_ms": 150.0,
                    "has_error": True,
                    "status_code": 2,
                    "status_code_string": "Error",
                    "http_method": "GET",
                    "http_url": "/api/health",
                    "kind_string": "Server",
                    "service_name": kwargs.get("service") or "api",
                },
            ],
        }

    def query_trace_summary(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_traces",
            "available": True,
            "total_spans": 100,
            "error_spans": 5,
            "error_rate": 0.05,
            "p99_ms": 250.0,
            "p95_ms": 180.0,
            "avg_ms": 120.0,
            "max_ms": 500.0,
        }


class TestQuerySignozLogs:
    def test_backend_injection(self) -> None:
        backend = _FakeSigNozBackend()
        result = query_signoz_logs(
            service="api",
            time_range_minutes=60,
            severity="ERROR",
            limit=10,
            signoz_backend=backend,
        )
        assert result["source"] == "signoz_logs"
        assert result["available"] is True
        assert result["total"] == 2
        assert len(result["logs"]) == 2
        assert len(result["error_logs"]) == 1
        assert result["error_logs"][0]["severity"] == "ERROR"

    def test_not_configured_without_backend(self) -> None:
        result = query_signoz_logs(service="api")
        assert result["source"] == "signoz_logs"
        assert result["available"] is False
        assert "not configured" in result.get("error", "").lower()


class TestQuerySignozMetrics:
    def test_backend_injection(self) -> None:
        backend = _FakeSigNozBackend()
        result = query_signoz_metrics(
            metric_name="cpu_usage",
            service="api",
            time_range_minutes=60,
            aggregation="avg",
            limit=10,
            signoz_backend=backend,
        )
        assert result["source"] == "signoz_metrics"
        assert result["available"] is True
        assert result["metric_name"] == "cpu_usage"
        assert len(result["metrics"]) == 1

    def test_not_configured_without_backend(self) -> None:
        result = query_signoz_metrics(metric_name="cpu_usage")
        assert result["source"] == "signoz_metrics"
        assert result["available"] is False
        assert "not configured" in result.get("error", "").lower()


class TestQuerySignozTraces:
    def test_backend_injection(self) -> None:
        backend = _FakeSigNozBackend()
        result = query_signoz_traces(
            service="api",
            time_range_minutes=60,
            error_only=True,
            limit=10,
            signoz_backend=backend,
        )
        assert result["source"] == "signoz_traces"
        assert result["available"] is True
        assert result["total"] == 1
        assert result["summary"]["error_rate"] == 0.05

    def test_not_configured_without_backend(self) -> None:
        result = query_signoz_traces(service="api")
        assert result["source"] == "signoz_traces"
        assert result["available"] is False
        assert "not configured" in result.get("error", "").lower()
