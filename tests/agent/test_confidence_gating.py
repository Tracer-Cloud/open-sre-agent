"""Tests for confidence band classification and evidence sufficiency gating."""

from __future__ import annotations

import pytest

from app.agent.result import (
    InvestigationResult,
    check_sufficiency,
    classify_confidence_band,
)

# ---------------------------------------------------------------------------
# classify_confidence_band
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score, expected",
    [
        (1.0, "high"),
        (0.75, "high"),
        (0.74, "medium"),
        (0.40, "medium"),
        (0.39, "low"),
        (0.0, "low"),
    ],
)
def test_classify_confidence_band_thresholds(score: float, expected: str) -> None:
    assert classify_confidence_band(score) == expected


# ---------------------------------------------------------------------------
# InvestigationResult factory classmethods set correct band
# ---------------------------------------------------------------------------


def test_unknown_result_has_low_band() -> None:
    result = InvestigationResult.unknown("test-alert")
    assert result.confidence_band == "low"
    assert result.validity_score == 0.0


def test_noise_result_has_high_band() -> None:
    result = InvestigationResult.noise()
    assert result.confidence_band == "high"
    assert result.validity_score == 1.0


# ---------------------------------------------------------------------------
# check_sufficiency — three scenarios from the issue
# ---------------------------------------------------------------------------


def test_sufficient_evidence_passes_gate() -> None:
    """High score + multiple validated claims → definitive, no prefix needed."""
    result = InvestigationResult(
        root_cause="DB connection pool exhausted due to query buildup.",
        root_cause_category="database",
        validity_score=0.85,
        confidence_band="high",
        validated_claims=[
            {"claim": "Connection pool at 100%", "validation_status": "validated"},
            {"claim": "Query latency spiked at 14:32 UTC", "validation_status": "validated"},
        ],
    )
    assert check_sufficiency(result) is True


def test_weak_evidence_fails_gate() -> None:
    """Low score + no validated claims → gate fires, root cause should be prefixed."""
    result = InvestigationResult(
        root_cause="Suspected memory leak in worker process.",
        root_cause_category="performance",
        validity_score=0.30,
        confidence_band="low",
        validated_claims=[],
    )
    assert check_sufficiency(result) is False
    # Simulate gate behaviour applied in investigation.py
    if not result.root_cause.startswith("Most likely"):
        result.root_cause = f"Most likely: {result.root_cause}"
    assert result.root_cause.startswith("Most likely:")


def test_conflicting_evidence_medium_band_fails_gate() -> None:
    """Medium score with only one validated claim — gate fires."""
    result = InvestigationResult(
        root_cause="Possible network partition or config drift after deployment.",
        root_cause_category="network",
        validity_score=0.55,
        confidence_band="medium",
        validated_claims=[
            {"claim": "Packet loss observed on inter-AZ traffic", "validation_status": "validated"},
        ],
        ranked_hypotheses=["Network partition between AZs", "Config drift after last deploy"],
        missing_evidence=[
            "VPC flow logs for the affected subnets",
            "Deployment history for the last 2 hours",
        ],
    )
    assert classify_confidence_band(result.validity_score) == "medium"
    assert check_sufficiency(result) is False
    assert len(result.ranked_hypotheses) == 2
    assert len(result.missing_evidence) == 2


def test_medium_score_with_two_validated_claims_passes_gate() -> None:
    """Medium score but 2+ validated claims is considered sufficient."""
    result = InvestigationResult(
        root_cause="High CPU due to unindexed query on orders table.",
        root_cause_category="database",
        validity_score=0.60,
        confidence_band="medium",
        validated_claims=[
            {"claim": "CPU at 95% on RDS instance", "validation_status": "validated"},
            {"claim": "Slow query log shows full-table scan", "validation_status": "validated"},
        ],
    )
    assert check_sufficiency(result) is True


def test_high_score_with_no_validated_claims_fails_gate() -> None:
    """High validity_score but zero validated claims must not pass — LLM self-report alone insufficient."""
    result = InvestigationResult(
        root_cause="DB connection pool exhausted.",
        root_cause_category="database",
        validity_score=0.85,
        confidence_band="high",
        validated_claims=[],
    )
    assert check_sufficiency(result) is False


def test_healthy_category_always_passes_gate() -> None:
    """Healthy findings are always definitive regardless of score."""
    result = InvestigationResult(
        root_cause="All systems operating normally — no incident detected.",
        root_cause_category="healthy",
        validity_score=0.20,
        confidence_band="low",
    )
    assert check_sufficiency(result) is True
