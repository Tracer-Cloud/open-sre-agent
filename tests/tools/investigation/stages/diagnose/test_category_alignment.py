from __future__ import annotations

import pytest

from core.domain.diagnosis.alignment import (
    apply_category_alignment_adjustments,
    detect_category_text_mismatch,
)


def test_detect_mismatch_when_text_signals_database_but_category_is_network() -> None:
    reason = detect_category_text_mismatch(
        "Redis connection pool was exhausted and rejected new clients.",
        "dns_resolution_failure",
    )

    assert reason is not None
    assert "database" in reason


def test_no_mismatch_when_category_matches_text() -> None:
    assert (
        detect_category_text_mismatch(
            "Container exceeded its memory limit and was OOMKilled by the kubelet.",
            "pod_oomkilled",
        )
        is None
    )


def test_no_mismatch_for_cpu_only_incident() -> None:
    assert (
        detect_category_text_mismatch(
            "The container hit its CPU limit and is being throttled, raising latency.",
            "pod_cpu_throttled",
        )
        is None
    )


def test_no_mismatch_when_category_group_is_not_in_signal_map() -> None:
    assert (
        detect_category_text_mismatch(
            "A bad deploy exhausted the Redis connection pool and Postgres replication lag spiked.",
            "bad_deploy",
        )
        is None
    )


def test_apply_adjustments_lowers_validity_and_adds_recommendation() -> None:
    score, recommendations, mismatch, reason = apply_category_alignment_adjustments(
        root_cause="Redis connection pool was exhausted and rejected new clients.",
        root_cause_category="dns_resolution_failure",
        validity_score=0.85,
        investigation_recommendations=[],
    )

    assert score == pytest.approx(0.7)
    assert mismatch is True
    assert reason is not None
    assert len(recommendations) == 1
    assert "review the classification" in recommendations[0]


def test_skip_alignment_for_healthy_and_unknown() -> None:
    assert detect_category_text_mismatch("All metrics normal.", "healthy") is None
    assert detect_category_text_mismatch("Not enough evidence.", "unknown") is None
