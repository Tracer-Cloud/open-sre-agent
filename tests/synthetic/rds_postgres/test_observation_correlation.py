from __future__ import annotations

from datetime import UTC, datetime

from app.agent.correlation.runtime import build_runtime_correlation
from app.agent.correlation.upstream import (
    LogSignal,
    MetricSeries,
    TopologyHint,
    UpstreamEvidenceBundle,
)
from tests.synthetic.rds_postgres.observations import (
    build_observation,
    compute_trajectory_metrics,
)


def test_build_observation_includes_correlation_payload_in_canonical_report() -> None:
    trajectory = compute_trajectory_metrics(
        executed_hypotheses=[],
        golden=[],
        loops_used=0,
        max_loops=None,
    )
    correlation = {
        "correlated_signals": [
            {
                "source": "aws_cloudwatch_metrics",
                "name": "EC2WebTierCPU",
                "description": "Web tier CPU rose with RDS CPU.",
                "score": 1.0,
            }
        ],
        "most_likely_causal_drivers": [
            {
                "name": "orders-web-asg",
                "tier": "web",
                "confidence": 0.95,
                "correlated_signals": [],
                "rationale": "Web tier is time-aligned and topology-adjacent.",
            }
        ],
    }

    observation = build_observation(
        scenario_id="015-mysql-ec2-load-attribution",
        suite="rds-postgres",
        backend="fixture",
        score={
            "passed": True,
            "actual_category": "application_tier_load_spike",
            "failure_reasons": [],
            "gates": {},
        },
        reasoning=None,
        correlation=correlation,
        trajectory=trajectory,
        evaluated_golden_actions=[],
        trajectory_policy=None,
        final_state={"evidence": {}},
        available_evidence_sources=[],
        required_evidence_sources=[],
        started_at=datetime(2026, 4, 15, tzinfo=UTC),
        wall_time_s=0.1,
    )

    assert observation.correlation == correlation
    assert observation.canonical_report_payload["correlation"] == correlation
    assert list(observation.canonical_report_payload["correlation"]) == [
        "correlated_signals",
        "most_likely_causal_drivers",
    ]


def test_runtime_correlation_smoke_payload_shape() -> None:
    evidence = UpstreamEvidenceBundle(
        rds_metrics=(
            MetricSeries(
                source="datadog",
                name="aws.rds.cpuutilization",
                timestamps=(
                    "2026-04-15T14:00:00Z",
                    "2026-04-15T14:01:00Z",
                    "2026-04-15T14:02:00Z",
                ),
                values=(35.0, 92.0, 95.0),
            ),
        ),
        upstream_metrics=(
            MetricSeries(
                source="datadog",
                name="system.cpu.user{service:orders-web}",
                timestamps=(
                    "2026-04-15T14:00:00Z",
                    "2026-04-15T14:01:00Z",
                    "2026-04-15T14:02:00Z",
                ),
                values=(30.0, 90.0, 94.0),
            ),
        ),
        web_request_logs=(
            LogSignal(
                source="datadog",
                name="alb",
                timestamps=("2026-04-15T14:01:00Z",),
                messages=("GET /checkout 200",),
            ),
        ),
        app_logs=(
            LogSignal(
                source="datadog",
                name="orders-app",
                timestamps=("2026-04-15T14:01:00Z",),
                messages=("checkout fanout started",),
            ),
        ),
        topology_hints=(
            TopologyHint(
                source="system.cpu.user{service:orders-web}",
                target="orders-rds-prod",
                relation="upstream_of",
            ),
        ),
        operator_hints=("scheduled checkout sync introduced recently",),
    )

    result = build_runtime_correlation(
        evidence,
        target_resource="orders-rds-prod",
    )

    assert "correlated_signals" in result
    assert "most_likely_causal_drivers" in result

    signals = result["correlated_signals"]

    assert signals
    assert {"name", "source", "score"} <= set(signals[0])

    drivers = result["most_likely_causal_drivers"]

    assert drivers
    assert drivers[0]["confidence"] > 0.0
    assert "rationale" in drivers[0]
