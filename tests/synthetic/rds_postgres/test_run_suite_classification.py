"""Direct unit tests for the TP / FP / TN / FN classifier and aggregation.

The classifier (`classify_outcome`) maps an agent outcome against ground truth
into one of four cells. The aggregator (`compute_classification_stats`) tallies
counts and computes accuracy / precision / recall / F1 across a result set.
Both are pure and have no dependency on the rest of the suite, so the tests
exercise them directly.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.synthetic.rds_postgres.run_suite import (
    ScenarioScore,
    classify_outcome,
    compute_classification_stats,
)

# ---------------------------------------------------------------------------
# classify_outcome — truth table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "expected", "present", "want"),
    [
        # Real-fault scenario, agent identifies the right category.
        ("resource_exhaustion", "resource_exhaustion", True, "TP"),
        ("infrastructure", "infrastructure", True, "TP"),
        # Real-fault scenario, agent says "healthy" or has no root cause.
        ("healthy", "resource_exhaustion", True, "FN"),
        ("unknown", "resource_exhaustion", False, "FN"),
        # Real-fault scenario, agent says wrong non-healthy category.
        ("infrastructure", "resource_exhaustion", True, "FN"),
        ("cpu_saturation", "resource_exhaustion", True, "FN"),
        # Healthy scenario, agent correctly says healthy or has no root cause.
        ("healthy", "healthy", True, "TN"),
        ("unknown", "healthy", False, "TN"),
        # Healthy scenario, agent claims a fault.
        ("resource_exhaustion", "healthy", True, "FP"),
        ("infrastructure", "healthy", True, "FP"),
    ],
)
def test_classify_outcome_truth_table(actual: str, expected: str, present: bool, want: str) -> None:
    assert classify_outcome(actual, expected, present) == want


def test_classify_outcome_returns_one_of_four_classes() -> None:
    """Sanity: every output is one of TP / FP / TN / FN, never None."""
    for actual in ("healthy", "resource_exhaustion", "unknown"):
        for expected in ("healthy", "resource_exhaustion"):
            for present in (True, False):
                got = classify_outcome(actual, expected, present)
                assert got in {"TP", "FP", "TN", "FN"}


# ---------------------------------------------------------------------------
# compute_classification_stats — aggregation + derived metrics
# ---------------------------------------------------------------------------


@dataclass
class _MockScore:
    """Minimal stand-in for ScenarioScore — only `outcome_class` is read."""

    outcome_class: str | None


def _scores(*classes: str | None) -> list[_MockScore]:
    return [_MockScore(outcome_class=c) for c in classes]


def test_compute_stats_empty_input_returns_zeros() -> None:
    stats = compute_classification_stats([])
    assert stats["TP"] == 0
    assert stats["FP"] == 0
    assert stats["TN"] == 0
    assert stats["FN"] == 0
    assert stats["total"] == 0
    assert stats["accuracy"] == 0.0
    assert stats["precision"] == 0.0
    assert stats["recall"] == 0.0
    assert stats["f1"] == 0.0


def test_compute_stats_counts_each_class() -> None:
    stats = compute_classification_stats(_scores("TP", "TP", "FP", "TN", "FN", "FN"))
    assert stats["TP"] == 2
    assert stats["FP"] == 1
    assert stats["TN"] == 1
    assert stats["FN"] == 2
    assert stats["total"] == 6


def test_compute_stats_accuracy_precision_recall_f1_math() -> None:
    """Spot-check the arithmetic against hand-computed values.

    With TP=2, FP=1, TN=1, FN=2:
      accuracy = (2+1)/6 = 0.5
      precision = 2/(2+1) = 2/3
      recall = 2/(2+2) = 0.5
      F1 = 2 * P * R / (P + R) = 2 * (2/3) * (1/2) / (2/3 + 1/2) = 4/7
    """
    stats = compute_classification_stats(_scores("TP", "TP", "FP", "TN", "FN", "FN"))
    assert stats["accuracy"] == pytest.approx(0.5)
    assert stats["precision"] == pytest.approx(2 / 3)
    assert stats["recall"] == pytest.approx(0.5)
    assert stats["f1"] == pytest.approx(4 / 7)


def test_compute_stats_all_correct_yields_one_one_one() -> None:
    """An all-passing run is the upper bound: all metrics = 1.0."""
    stats = compute_classification_stats(_scores("TP", "TP", "TN"))
    assert stats["accuracy"] == 1.0
    assert stats["precision"] == 1.0
    assert stats["recall"] == 1.0
    assert stats["f1"] == 1.0


def test_compute_stats_all_failing_yields_zero_accuracy() -> None:
    stats = compute_classification_stats(_scores("FP", "FN", "FN"))
    assert stats["accuracy"] == 0.0
    # precision = 0 / (0 + 1) = 0; recall = 0 / (0 + 2) = 0; F1 = 0.
    assert stats["precision"] == 0.0
    assert stats["recall"] == 0.0
    assert stats["f1"] == 0.0


def test_compute_stats_skips_unknown_outcome_class() -> None:
    """Unknown / None outcome_class is treated as not classified, not counted."""
    stats = compute_classification_stats(_scores("TP", None, "garbage", "TN"))
    assert stats["TP"] == 1
    assert stats["TN"] == 1
    assert stats["total"] == 2  # only two classified


def test_compute_stats_division_by_zero_does_not_raise() -> None:
    """All-TN result set: TP+FP=0 (precision denom) and TP+FN=0 (recall denom).
    Both must short-circuit to 0.0 rather than ZeroDivisionError."""
    stats = compute_classification_stats(_scores("TN", "TN"))
    assert stats["accuracy"] == 1.0
    assert stats["precision"] == 0.0
    assert stats["recall"] == 0.0
    assert stats["f1"] == 0.0


# ---------------------------------------------------------------------------
# ScenarioScore field is wired
# ---------------------------------------------------------------------------


def test_scenario_score_outcome_class_defaults_to_none() -> None:
    score = ScenarioScore(
        scenario_id="000-healthy",
        passed=True,
        root_cause_present=False,
        expected_category="healthy",
        actual_category="healthy",
        missing_keywords=[],
        matched_keywords=[],
        root_cause="",
    )
    assert score.outcome_class is None


def test_scenario_score_outcome_class_can_be_set() -> None:
    score = ScenarioScore(
        scenario_id="002-connection-exhaustion",
        passed=True,
        root_cause_present=True,
        expected_category="resource_exhaustion",
        actual_category="resource_exhaustion",
        missing_keywords=[],
        matched_keywords=["connection"],
        root_cause="connection exhaustion",
        outcome_class="TP",
    )
    assert score.outcome_class == "TP"
