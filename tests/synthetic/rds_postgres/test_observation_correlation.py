from __future__ import annotations

from datetime import UTC, datetime

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
