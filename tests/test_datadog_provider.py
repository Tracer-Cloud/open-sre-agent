from __future__ import annotations

from app.integrations.datadog.correlation.adapter import (
    DatadogCorrelationAdapter,
)
from app.integrations.datadog.correlation.provider import (
    DatadogCorrelationQueries,
    DatadogUpstreamEvidenceProvider,
)


def test_datadog_upstream_provider_collects_metrics_logs_and_topology() -> None:
    metric_queries: list[str] = []
    log_queries: list[str] = []

    adapter = DatadogCorrelationAdapter(
        metric_query_fn=lambda metric, _params: (
            metric_queries.append(metric)
            or {
                "timestamps": (
                    "2026-04-15T14:00:00Z",
                    "2026-04-15T14:01:00Z",
                ),
                "values": (40.0, 80.0),
            }
        ),
        log_query_fn=lambda query, _params: (
            log_queries.append(query)
            or {
                "timestamps": (
                    "2026-04-15T14:00:00Z",
                    "2026-04-15T14:01:00Z",
                ),
                "messages": ("GET /checkout 200", "checkout fanout started"),
            }
        ),
    )

    provider = DatadogUpstreamEvidenceProvider(
        adapter=adapter,
        target_resource="orders-rds-prod",
    )

    bundle = provider.collect_upstream_evidence(
        alert_id="alert-1",
        service_name="orders",
        window_start="2026-04-15T14:00:00Z",
        window_end="2026-04-15T14:15:00Z",
    )

    assert metric_queries == [
        "aws.rds.cpuutilization{dbinstanceidentifier:orders-rds-prod}",
        "aws.rds.database_connections{dbinstanceidentifier:orders-rds-prod}",
        "system.cpu.user{service:orders}",
    ]
    assert log_queries == [
        "service:orders source:alb",
        "service:orders",
    ]

    assert len(bundle.rds_metrics) == 2
    assert len(bundle.upstream_metrics) == 1
    assert bundle.web_request_logs[0].source == "datadog"
    assert bundle.app_logs[0].source == "datadog"
    assert bundle.topology_hints[0].relation == "upstream_of"
    assert bundle.topology_hints[0].target == "orders-rds-prod"


def test_datadog_upstream_provider_can_query_multiple_candidate_services() -> None:
    metric_queries: list[str] = []
    adapter = DatadogCorrelationAdapter(
        metric_query_fn=lambda metric, _params: (
            metric_queries.append(metric)
            or {
                "timestamps": ("2026-04-15T14:00:00Z",),
                "values": (40.0,),
            }
        ),
        log_query_fn=lambda _query, _params: {
            "timestamps": (),
            "messages": (),
        },
    )
    provider = DatadogUpstreamEvidenceProvider(
        adapter=adapter,
        queries=DatadogCorrelationQueries(upstream_service_names=("orders-web", "checkout-api")),
        target_resource="orders-rds-prod",
    )

    bundle = provider.collect_upstream_evidence(
        alert_id="alert-1",
        service_name="orders",
        window_start="2026-04-15T14:00:00Z",
        window_end="2026-04-15T14:15:00Z",
    )

    assert "system.cpu.user{service:orders-web}" in metric_queries
    assert "system.cpu.user{service:checkout-api}" in metric_queries
    assert [hint.target for hint in bundle.topology_hints] == [
        "orders-rds-prod",
        "orders-rds-prod",
    ]


def test_datadog_upstream_provider_preserves_prescoped_rds_metrics() -> None:
    metric_queries: list[str] = []

    adapter = DatadogCorrelationAdapter(
        metric_query_fn=lambda metric, _params: (
            metric_queries.append(metric)
            or {
                "timestamps": ("2026-04-15T14:00:00Z",),
                "values": (40.0,),
            }
        ),
        log_query_fn=lambda _query, _params: {
            "timestamps": (),
            "messages": (),
        },
    )

    provider = DatadogUpstreamEvidenceProvider(
        adapter=adapter,
        queries=DatadogCorrelationQueries(
            rds_cpu_metric="aws.rds.cpuutilization{dbinstanceidentifier:custom-rds}",
            rds_connections_metric=(
                "aws.rds.database_connections{dbinstanceidentifier:custom-rds}"
            ),
        ),
        target_resource="orders-rds-prod",
    )

    provider.collect_upstream_evidence(
        alert_id="alert-1",
        service_name="orders",
        window_start="2026-04-15T14:00:00Z",
        window_end="2026-04-15T14:15:00Z",
    )

    assert metric_queries[:2] == [
        "aws.rds.cpuutilization{dbinstanceidentifier:custom-rds}",
        "aws.rds.database_connections{dbinstanceidentifier:custom-rds}",
    ]


def test_datadog_upstream_provider_adds_instance_scope_to_tagged_rds_metrics() -> None:
    metric_queries: list[str] = []

    adapter = DatadogCorrelationAdapter(
        metric_query_fn=lambda metric, _params: (
            metric_queries.append(metric)
            or {
                "timestamps": ("2026-04-15T14:00:00Z",),
                "values": (40.0,),
            }
        ),
        log_query_fn=lambda _query, _params: {
            "timestamps": (),
            "messages": (),
        },
    )

    provider = DatadogUpstreamEvidenceProvider(
        adapter=adapter,
        queries=DatadogCorrelationQueries(
            rds_cpu_metric="aws.rds.cpuutilization{env:prod}",
            rds_connections_metric="aws.rds.database_connections{env:prod}",
        ),
        target_resource="orders-rds-prod",
    )

    provider.collect_upstream_evidence(
        alert_id="alert-1",
        service_name="orders",
        window_start="2026-04-15T14:00:00Z",
        window_end="2026-04-15T14:15:00Z",
    )

    assert metric_queries[:2] == [
        "aws.rds.cpuutilization{env:prod,dbinstanceidentifier:orders-rds-prod}",
        "aws.rds.database_connections{env:prod,dbinstanceidentifier:orders-rds-prod}",
    ]


def test_datadog_upstream_provider_does_not_scope_unknown_target_for_tagged_metrics() -> None:
    metric_queries: list[str] = []

    adapter = DatadogCorrelationAdapter(
        metric_query_fn=lambda metric, _params: (
            metric_queries.append(metric)
            or {
                "timestamps": ("2026-04-15T14:00:00Z",),
                "values": (40.0,),
            }
        ),
        log_query_fn=lambda _query, _params: {
            "timestamps": (),
            "messages": (),
        },
    )

    provider = DatadogUpstreamEvidenceProvider(
        adapter=adapter,
        queries=DatadogCorrelationQueries(
            rds_cpu_metric="aws.rds.cpuutilization{env:prod}",
            rds_connections_metric="aws.rds.database_connections{env:prod}",
        ),
    )

    provider.collect_upstream_evidence(
        alert_id="alert-1",
        service_name="orders",
        window_start="2026-04-15T14:00:00Z",
        window_end="2026-04-15T14:15:00Z",
    )

    assert metric_queries[:2] == [
        "aws.rds.cpuutilization{env:prod}",
        "aws.rds.database_connections{env:prod}",
    ]
