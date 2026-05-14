from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from app.correlation.upstream import UpstreamEvidenceBundle
from app.pipeline.pipeline import (
    _build_correlation_config,
    _candidate_services_from_state,
    _datadog_avg_query,
    _target_resource_from_state,
)


def test_datadog_avg_query_preserves_existing_scope() -> None:
    assert _datadog_avg_query("system.cpu.user{service:orders}") == (
        "avg:system.cpu.user{service:orders}"
    )
    assert _datadog_avg_query("aws.rds.cpuutilization") == "avg:aws.rds.cpuutilization{*}"
    assert _datadog_avg_query("avg:custom.metric{env:prod}") == "avg:custom.metric{env:prod}"


def test_correlation_config_state_helpers_use_compatible_fallbacks() -> None:
    assert _target_resource_from_state({}) == "unknown-rds"
    assert _candidate_services_from_state({"raw_alert": {"upstream_services": "api, worker"}}) == (
        "api",
        "worker",
    )


def test_correlation_config_uses_alert_resource_and_scoped_metric_query() -> None:
    metric_queries: list[str] = []

    def query_metrics(
        _self: object,
        query: str,
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        assert start.tzinfo == UTC
        assert end.tzinfo == UTC
        metric_queries.append(query)
        return {
            "success": True,
            "timestamps": ["2026-05-14T10:00:00Z", "2026-05-14T10:05:00Z"],
            "values": [40.0, 80.0],
        }

    def search_logs(
        _self: object,
        _query: str,
        time_range_minutes: int = 60,
        limit: int = 50,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        assert time_range_minutes == 15
        assert limit == 100
        assert start == datetime(2026, 5, 14, 10, 0, tzinfo=UTC)
        assert end == datetime(2026, 5, 14, 10, 15, tzinfo=UTC)
        return {"success": True, "logs": []}

    state: dict[str, Any] = {
        "resolved_integrations": {
            "datadog": {
                "api_key": "api-key",
                "app_key": "app-key",
                "site": "datadoghq.com",
            }
        },
        "raw_alert": {
            "resource": "orders-rds-prod",
            "candidate_services": ["orders-web", "checkout-api"],
        },
    }

    with (
        patch("app.services.datadog.client.DatadogClient.query_metrics", query_metrics),
        patch("app.services.datadog.client.DatadogClient.search_logs", search_logs),
    ):
        config = _build_correlation_config(state)
        assert config is not None
        provider = config["configurable"]["upstream_evidence_provider"]
        bundle: UpstreamEvidenceBundle = provider.collect_upstream_evidence(
            alert_id="alert-1",
            service_name="orders",
            window_start="2026-05-14T10:00:00Z",
            window_end="2026-05-14T10:15:00Z",
        )

    assert "avg:system.cpu.user{service:orders-web}" in metric_queries
    assert "avg:system.cpu.user{service:checkout-api}" in metric_queries
    assert "avg:system.cpu.user{service:orders}{*}" not in metric_queries
    assert bundle.topology_hints[0].target == "orders-rds-prod"
