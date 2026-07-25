from __future__ import annotations

from core.domain.correlation.scoring import (
    rank_upstream_candidates,
    score_periodic_spikes,
    score_time_window_correlation,
    score_topology_adjacency,
)
from core.domain.types.upstream import TimeSeries, TopologyNode, UpstreamCandidate


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


def test_score_periodic_spikes_counts_distinct_threshold_crossings() -> None:
    score = score_periodic_spikes(
        signal_name="upstream_cpu",
        values=(20.0, 82.0, 30.0, 85.0, 28.0, 88.0),
        spike_threshold=80.0,
    )

    assert score.repeated_spikes == 3
    assert score.score == 1.0


def test_score_periodic_spikes_treats_single_sustained_spike_as_one_event() -> None:
    score = score_periodic_spikes(
        signal_name="upstream_cpu",
        values=(20.0, 90.0, 90.0, 90.0, 20.0),
        spike_threshold=80.0,
    )

    assert score.repeated_spikes == 1
    assert score.score == 0.0


def test_score_periodic_spikes_does_not_count_window_start_elevation() -> None:
    score = score_periodic_spikes(
        signal_name="upstream_cpu",
        values=(90.0, 20.0, 90.0),
        spike_threshold=80.0,
    )

    assert score.repeated_spikes == 1
    assert score.score == 0.0


def test_score_periodic_spikes_detects_recurrence_that_starts_elevated() -> None:
    score = score_periodic_spikes(
        signal_name="upstream_cpu",
        values=(90.0, 20.0, 90.0, 20.0, 90.0),
        spike_threshold=80.0,
    )

    assert score.repeated_spikes == 2
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
