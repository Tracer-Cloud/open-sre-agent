from __future__ import annotations

from app.correlation.upstream import (
    LogSignal,
    MetricSeries,
    TopologyHint,
    UpstreamEvidenceBundle,
)
from tests.synthetic.rds_postgres.correlation.investigation_flow import (
    investigate_upstream_candidates,
)


def test_investigation_flow_returns_ranked_candidates_without_trace_ids() -> None:
    timestamps = (
        "2026-04-15T14:00:00Z",
        "2026-04-15T14:01:00Z",
        "2026-04-15T14:02:00Z",
        "2026-04-15T14:03:00Z",
    )

    rds_metric = MetricSeries(
        source="cloudwatch",
        name="RDS CPU",
        timestamps=timestamps,
        values=(20.0, 40.0, 60.0, 80.0),
    )

    web_metric = MetricSeries(
        source="datadog",
        name="orders-web-asg",
        timestamps=timestamps,
        values=(10.0, 30.0, 50.0, 70.0),
    )

    worker_metric = MetricSeries(
        source="datadog",
        name="orders-worker-asg",
        timestamps=timestamps,
        values=(10.0, 10.0, 10.0, 10.0),
    )

    evidence = UpstreamEvidenceBundle(
        rds_metrics=(rds_metric,),
        upstream_metrics=(web_metric, worker_metric),
        web_request_logs=(
            LogSignal(
                source="alb",
                name="orders-alb-access",
                timestamps=timestamps,
                messages=("GET /checkout 200", "GET /checkout 200"),
            ),
        ),
        app_logs=(
            LogSignal(
                source="app",
                name="orders-app",
                timestamps=timestamps,
                messages=("checkout fanout started", "checkout fanout completed"),
            ),
        ),
        topology_hints=(
            TopologyHint(
                source="orders-web-asg",
                target="orders-prod-mysql",
                relation="upstream_of",
            ),
        ),
        operator_hints=("scheduled automation feature was recently introduced",),
    )

    report = investigate_upstream_candidates(
        evidence=evidence,
    )

    assert report.correlated_signals
    assert report.most_likely_causal_drivers[0].name == "orders-web-asg"

    assert (
        report.most_likely_causal_drivers[0].confidence
        > report.most_likely_causal_drivers[1].confidence
    )

    assert "trace" not in report.most_likely_causal_drivers[0].rationale.lower()
