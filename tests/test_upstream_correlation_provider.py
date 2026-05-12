from __future__ import annotations

from app.correlation.providers import (
    NoopUpstreamEvidenceProvider,
    QueryBackedUpstreamEvidenceProvider,
)
from app.correlation.upstream import (
    LogSignal,
    MetricSeries,
    TopologyHint,
    UpstreamEvidenceBundle,
)


def test_noop_upstream_evidence_provider_returns_empty_bundle() -> None:
    provider = NoopUpstreamEvidenceProvider()

    bundle = provider.collect_upstream_evidence(
        alert_id="synthetic-alert",
        service_name="orders",
        window_start="2026-04-15T14:00:00Z",
        window_end="2026-04-15T14:15:00Z",
    )

    assert isinstance(bundle, UpstreamEvidenceBundle)
    assert bundle.rds_metrics == ()
    assert bundle.upstream_metrics == ()
    assert bundle.web_request_logs == ()
    assert bundle.app_logs == ()
    assert bundle.topology_hints == ()
    assert bundle.operator_hints == ()


def test_query_backed_provider_collects_metrics_logs_topology_and_hints() -> None:
    timestamps = ("2026-04-15T14:00:00Z", "2026-04-15T14:01:00Z")

    provider = QueryBackedUpstreamEvidenceProvider(
        rds_metric_query=lambda service, _start, _end: (
            MetricSeries(
                source="cloudwatch",
                name=f"{service}-rds-cpu",
                timestamps=timestamps,
                values=(40.0, 80.0),
            ),
        ),
        upstream_metric_query=lambda service, _start, _end: (
            MetricSeries(
                source="datadog",
                name=f"{service}-web-cpu",
                timestamps=timestamps,
                values=(35.0, 75.0),
            ),
        ),
        web_log_query=lambda service, _start, _end: (
            LogSignal(
                source="alb",
                name=f"{service}-alb-access",
                timestamps=timestamps,
                messages=("GET /checkout 200", "GET /checkout 200"),
            ),
        ),
        app_log_query=lambda service, _start, _end: (
            LogSignal(
                source="app",
                name=f"{service}-app",
                timestamps=timestamps,
                messages=("checkout fanout started", "checkout fanout completed"),
            ),
        ),
        topology_query=lambda service: (
            TopologyHint(
                source=f"{service}-web",
                target=f"{service}-rds",
                relation="upstream_of",
            ),
        ),
        operator_hint_query=lambda _service: (
            "scheduled automation feature was recently introduced",
        ),
    )

    bundle = provider.collect_upstream_evidence(
        alert_id="alert-1",
        service_name="orders",
        window_start=timestamps[0],
        window_end=timestamps[1],
    )

    assert bundle.rds_metrics[0].source == "cloudwatch"
    assert bundle.upstream_metrics[0].source == "datadog"
    assert bundle.web_request_logs[0].source == "alb"
    assert bundle.app_logs[0].source == "app"
    assert bundle.topology_hints[0].relation == "upstream_of"
    assert bundle.operator_hints == ("scheduled automation feature was recently introduced",)
