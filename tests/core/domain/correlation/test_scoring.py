from __future__ import annotations

from core.domain.correlation.scoring import (
    TimeSeries,
    TopologyNode,
    rank_upstream_candidates,
    score_operator_hint,
    score_time_window_correlation,
    score_topology_adjacency,
)
from core.domain.types.upstream import UpstreamCandidate


def test_score_time_window_correlation_scores_matching_trends() -> None:
    timestamps = (
        "2026-04-15T14:00:00Z",
        "2026-04-15T14:01:00Z",
        "2026-04-15T14:02:00Z",
    )

    score = score_time_window_correlation(
        TimeSeries("rds_cpu", timestamps, (10.0, 20.0, 30.0)),
        TimeSeries("api_cpu", timestamps, (40.0, 50.0, 60.0)),
    )

    assert score.score == 1.0
    assert score.direction_matches == 2


def test_score_topology_adjacency_requires_target_relationship() -> None:
    score = score_topology_adjacency(
        source=TopologyNode("api", "service", ("orders-db",)),
        target=TopologyNode("orders-db", "rds", ()),
    )

    assert score.adjacency_score == 1.0


def test_score_operator_hint_matches_metric_tokens() -> None:
    score = score_operator_hint(
        metric_name="orders api latency",
        operator_hints=("orders api slow",),
    )
    assert score.score == 1.0


def test_rank_upstream_candidates_orders_by_confidence_then_name() -> None:
    ranked = rank_upstream_candidates(
        [
            UpstreamCandidate("checkout", "application", 0.5, (), ""),
            UpstreamCandidate("api", "application", 0.9, (), ""),
            UpstreamCandidate("worker", "application", 0.5, (), ""),
        ]
    )

    assert [candidate.name for candidate in ranked] == ["api", "checkout", "worker"]
