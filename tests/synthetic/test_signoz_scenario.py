"""Synthetic RCA scenario using SigNoz as the evidence source.

This test validates that a SigNoz alert triggers the correct tool seeding
and that the mock backend returns realistic fixture data.
"""

from __future__ import annotations

from typing import Any

from app.core.domain.alerts.alert_source import ALERT_SOURCE_TO_SEED_TOOL_SOURCES
from app.tools.SignozLogsTool import query_signoz_logs
from app.tools.SignozMetricsTool import query_signoz_metrics
from app.tools.SignozTracesTool import query_signoz_traces


class _FixtureSigNozBackend:
    """Minimal fixture backend for synthetic SigNoz scenarios."""

    def query_logs(self, **kwargs: Any) -> dict[str, Any]:
        service = kwargs.get("service") or "payment-service"
        return {
            "source": "signoz_logs",
            "available": True,
            "total": 3,
            "logs": [
                {
                    "timestamp": "2024-01-15T10:00:00Z",
                    "severity": "ERROR",
                    "severity_number": 17,
                    "message": f"Database connection timeout in {service}",
                    "trace_id": "trace-001",
                    "span_id": "span-001",
                    "attributes": {"http.method": "POST", "http.route": "/payments"},
                    "resources": {"service.name": service},
                },
                {
                    "timestamp": "2024-01-15T10:01:00Z",
                    "severity": "WARN",
                    "severity_number": 13,
                    "message": "Retry attempt 3/3 failed",
                    "trace_id": "trace-001",
                    "span_id": "span-002",
                    "attributes": {},
                    "resources": {"service.name": service},
                },
                {
                    "timestamp": "2024-01-15T10:02:00Z",
                    "severity": "INFO",
                    "severity_number": 9,
                    "message": "Circuit breaker opened",
                    "trace_id": "",
                    "span_id": "",
                    "attributes": {},
                    "resources": {"service.name": service},
                },
            ],
        }

    def query_metrics(self, **kwargs: Any) -> dict[str, Any]:
        service = kwargs.get("service") or "payment-service"
        metric = kwargs.get("metric_name", "cpu_usage")
        return {
            "source": "signoz_metrics",
            "available": True,
            "total": 2,
            "metric_name": metric,
            "resolved_metric": metric,
            "aggregation": kwargs.get("aggregation", "avg"),
            "metrics": [
                {
                    "interval": "2024-01-15 10:00:00",
                    "value": 85.5,
                    "metric_name": metric,
                    "service_name": service,
                },
                {
                    "interval": "2024-01-15 10:01:00",
                    "value": 92.3,
                    "metric_name": metric,
                    "service_name": service,
                },
            ],
        }

    def query_traces(self, **kwargs: Any) -> dict[str, Any]:
        service = kwargs.get("service") or "payment-service"
        return {
            "source": "signoz_traces",
            "available": True,
            "total": 2,
            "traces": [
                {
                    "timestamp": "2024-01-15T10:00:00Z",
                    "trace_id": "trace-001",
                    "span_id": "span-001",
                    "name": "POST /payments",
                    "duration_ms": 2500.0,
                    "has_error": True,
                    "status_code": 2,
                    "status_code_string": "Error",
                    "http_method": "POST",
                    "http_url": "/payments",
                    "kind_string": "Server",
                    "service_name": service,
                },
                {
                    "timestamp": "2024-01-15T10:00:01Z",
                    "trace_id": "trace-001",
                    "span_id": "span-002",
                    "name": "db.query",
                    "duration_ms": 2300.0,
                    "has_error": True,
                    "status_code": 2,
                    "status_code_string": "Error",
                    "http_method": "",
                    "http_url": "",
                    "kind_string": "Client",
                    "service_name": "postgres",
                },
            ],
        }

    def query_trace_summary(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_traces",
            "available": True,
            "total_spans": 50,
            "error_spans": 12,
            "error_rate": 0.24,
            "p99_ms": 2500.0,
            "p95_ms": 1800.0,
            "avg_ms": 1200.0,
            "max_ms": 2500.0,
        }


def test_signoz_alert_source_maps_to_tools() -> None:
    """SigNoz alert source seeds signoz tools before the ReAct loop."""
    assert "signoz" in ALERT_SOURCE_TO_SEED_TOOL_SOURCES
    assert ALERT_SOURCE_TO_SEED_TOOL_SOURCES["signoz"] == ("signoz",)


def test_signoz_logs_synthetic_scenario() -> None:
    """A synthetic SigNoz alert yields realistic log evidence."""
    backend = _FixtureSigNozBackend()
    result = query_signoz_logs(
        service="payment-service",
        time_range_minutes=60,
        severity="ERROR",
        limit=10,
        signoz_backend=backend,
    )
    assert result["available"] is True
    assert result["total"] == 3
    assert len(result["logs"]) == 3
    assert len(result["error_logs"]) >= 1
    assert "timeout" in result["error_logs"][0]["message"].lower()


def test_signoz_metrics_synthetic_scenario() -> None:
    """A synthetic SigNoz alert yields realistic metric evidence."""
    backend = _FixtureSigNozBackend()
    result = query_signoz_metrics(
        metric_name="cpu_usage",
        service="payment-service",
        time_range_minutes=60,
        aggregation="avg",
        limit=10,
        signoz_backend=backend,
    )
    assert result["available"] is True
    assert result["metric_name"] == "cpu_usage"
    assert len(result["metrics"]) == 2
    assert result["metrics"][0]["value"] == 85.5


def test_signoz_traces_synthetic_scenario() -> None:
    """A synthetic SigNoz alert yields realistic trace evidence."""
    backend = _FixtureSigNozBackend()
    result = query_signoz_traces(
        service="payment-service",
        time_range_minutes=60,
        error_only=True,
        limit=10,
        signoz_backend=backend,
    )
    assert result["available"] is True
    assert result["total"] == 2
    assert result["summary"]["error_rate"] == 0.24
    assert result["summary"]["p99_ms"] == 2500.0
    assert any(t["name"] == "POST /payments" for t in result["traces"])
