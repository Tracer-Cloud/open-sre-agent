"""Correlation ranking for scenario 001 (request burst on web tier)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.synthetic.rds_postgres.correlation.candidate_scoring import score_candidate_correlation
from tests.synthetic.rds_postgres.correlation.models import UpstreamCandidate
from tests.synthetic.rds_postgres.correlation.ranking import rank_upstream_candidates
from tests.synthetic.rds_postgres.correlation.time_window import (
    TimeSeries,
    score_time_window_correlation,
)
from tests.synthetic.rds_postgres.correlation.topology import TopologyNode, score_topology_adjacency

pytestmark = pytest.mark.synthetic

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "001-request-burst-ec2-app-tier"


def _load_json(filename: str) -> dict[str, Any]:
    return json.loads((SCENARIO_DIR / filename).read_text(encoding="utf-8"))


def _series(filename: str, name: str) -> TimeSeries:
    payload = _load_json(filename)
    return TimeSeries(
        name=name,
        timestamps=tuple(payload["timestamps"]),
        values=tuple(float(value) for value in payload["values"]),
    )


def test_request_burst_ranks_web_tier_above_worker_tier() -> None:
    rds_cpu = _series("aws_cloudwatch_metrics_CPUUtilization.json", "RDS CPUUtilization")
    web_cpu = _series("aws_cloudwatch_metrics_EC2WebTierCPU.json", "EC2 web tier CPU")
    worker_cpu = _series("aws_cloudwatch_metrics_EC2WorkerTierCPU.json", "EC2 worker tier CPU")

    rds_node = TopologyNode(name="orders-prod-mysql", node_type="rds_mysql", upstream_of=())
    web_node = TopologyNode(
        name="orders-web-asg", node_type="ec2_asg", upstream_of=("orders-prod-mysql",)
    )
    worker_node = TopologyNode(
        name="orders-worker-asg", node_type="ec2_asg", upstream_of=("orders-prod-mysql",)
    )

    web_score = score_candidate_correlation(
        candidate_name="orders-web-asg",
        time_window=score_time_window_correlation(rds_cpu, web_cpu),
        topology=score_topology_adjacency(source=web_node, target=rds_node),
    )
    worker_score = score_candidate_correlation(
        candidate_name="orders-worker-asg",
        time_window=score_time_window_correlation(rds_cpu, worker_cpu),
        topology=score_topology_adjacency(source=worker_node, target=rds_node),
    )

    ranked = rank_upstream_candidates(
        [
            UpstreamCandidate(
                name="orders-worker-asg",
                tier="worker",
                confidence=worker_score.final_confidence,
                correlated_signals=(),
                rationale=worker_score.rationale,
            ),
            UpstreamCandidate(
                name="orders-web-asg",
                tier="web",
                confidence=web_score.final_confidence,
                correlated_signals=(),
                rationale=web_score.rationale,
            ),
        ]
    )

    assert ranked[0].name == "orders-web-asg"
    assert ranked[0].confidence > ranked[1].confidence
