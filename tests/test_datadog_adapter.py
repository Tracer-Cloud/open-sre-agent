from __future__ import annotations

from app.correlation.datadog_adapter import (
    DatadogCorrelationAdapter,
)


def test_datadog_adapter_queries_metric_series() -> None:
    adapter = DatadogCorrelationAdapter(
        metric_query_fn=lambda _metric, _params: {
            "timestamps": (
                "2026-04-15T14:00:00Z",
                "2026-04-15T14:01:00Z",
            ),
            "values": (40.0, 80.0),
        },
        log_query_fn=lambda _query, _params: {
            "timestamps": (),
            "messages": (),
        },
    )

    metric = adapter.query_metric_series(
        metric_name="aws.rds.cpuutilization",
        start="2026-04-15T14:00:00Z",
        end="2026-04-15T14:15:00Z",
    )

    assert metric.source == "datadog"
    assert metric.name == "aws.rds.cpuutilization"
    assert metric.values == (40.0, 80.0)


def test_datadog_adapter_queries_logs() -> None:
    adapter = DatadogCorrelationAdapter(
        metric_query_fn=lambda _metric, _params: {
            "timestamps": (),
            "values": (),
        },
        log_query_fn=lambda _query, _params: {
            "timestamps": (
                "2026-04-15T14:00:00Z",
                "2026-04-15T14:01:00Z",
            ),
            "messages": (
                "GET /checkout 200",
                "GET /checkout 200",
            ),
        },
    )

    logs = adapter.query_logs(
        query="service:orders",
        start="2026-04-15T14:00:00Z",
        end="2026-04-15T14:15:00Z",
    )

    assert logs.source == "datadog"
    assert logs.messages[0] == "GET /checkout 200"


def test_datadog_adapter_tolerates_failure_payloads() -> None:
    adapter = DatadogCorrelationAdapter(
        metric_query_fn=lambda _metric, _params: {"success": False, "error": "boom"},
        log_query_fn=lambda _query, _params: {"success": False, "error": "boom"},
    )

    metric = adapter.query_metric_series(
        metric_name="aws.rds.cpuutilization",
        start="2026-04-15T14:00:00Z",
        end="2026-04-15T14:15:00Z",
    )
    logs = adapter.query_logs(
        query="service:orders",
        start="2026-04-15T14:00:00Z",
        end="2026-04-15T14:15:00Z",
    )

    assert metric.timestamps == ()
    assert metric.values == ()
    assert logs.timestamps == ()
    assert logs.messages == ()
