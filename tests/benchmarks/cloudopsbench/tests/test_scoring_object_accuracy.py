"""Tests for ``scoring.score_predictions`` object-localization metrics.

``object_a1`` and ``object_a3`` isolate "did the agent identify the right
service?" from "did the agent name the failure with the exact dataset
token?" The strict triple-match a1 conflates both. Adding these metrics
exposes which side of the gap a model is missing on — e.g., trainticket
runs collapse on localization (16% obj acc on failures) while boutique
runs nail the service but mis-pick the runtime cause label (69% obj acc
on failures). Without object_a1/object_a3 there is no way to read this
from the aggregate report.

These tests pin the contract: the metrics must NOT inherit a1's strict
triple-match — they must be true even when taxonomy or root_cause are
wrong, as long as fault_object is right.
"""

from __future__ import annotations

import pytest

from tests.benchmarks.cloudopsbench.scoring import score_predictions

GT = {
    "fault_taxonomy": "Runtime_Fault",
    "fault_object": "app/paymentservice",
    "root_cause": "liveness_probe_incorrect_port",
}


def _pred(taxonomy: str, fault_object: str, root_cause: str) -> dict[str, str]:
    return {
        "fault_taxonomy": taxonomy,
        "fault_object": fault_object,
        "root_cause": root_cause,
    }


def test_object_a1_rewards_correct_object_even_when_root_cause_wrong() -> None:
    """The whole point of object_a1: localization is rewarded independently
    of root-cause naming. Without this, an agent that perfectly identifies
    the failing service but mis-picks among tightly-clustered runtime
    causes scores zero across every outcome metric — the boutique failure
    pattern is invisible in the aggregate."""
    preds = [_pred("Runtime_Fault", "app/paymentservice", "oom_killed")]
    scores = score_predictions(preds, GT)
    assert scores["object_a1"] == 1.0
    assert scores["object_a3"] == 1.0
    # Triple-match metrics must still be zero — root_cause didn't match
    assert scores["a1"] == 0.0
    assert scores["partial_a1"] == 0.0


def test_object_a1_rewards_correct_object_even_when_taxonomy_wrong() -> None:
    """If the agent gets the right service AND the right root_cause but
    mis-labels the taxonomy, partial_a1 fires (object+root_cause both
    right) and object_a1 fires (object right). Only the strict triple
    a1 stays zero. Confirms object_a1 is taxonomy-independent."""
    preds = [_pred("Configuration_Fault", "app/paymentservice", "liveness_probe_incorrect_port")]
    scores = score_predictions(preds, GT)
    assert scores["object_a1"] == 1.0
    assert scores["partial_a1"] == 1.0  # object + root_cause both right
    assert scores["a1"] == 0.0  # taxonomy wrong


def test_object_a3_fires_when_correct_object_appears_at_rank_2() -> None:
    """11:46 run analysis showed 53% of failures had the correct
    fault_object in top-3 but only 38% at rank-1 — 15 points of object
    accuracy hiding in ranks 2-3. object_a3 must fire on any rank,
    object_a1 only on rank-1."""
    preds = [
        _pred("Runtime_Fault", "app/wrongservice", "some_cause"),
        _pred("Runtime_Fault", "app/paymentservice", "another_cause"),  # rank-2 hit
        _pred("Runtime_Fault", "app/anotherwrong", "third_cause"),
    ]
    scores = score_predictions(preds, GT)
    assert scores["object_a1"] == 0.0
    assert scores["object_a3"] == 1.0


def test_object_a3_fires_when_correct_object_appears_at_rank_3() -> None:
    preds = [
        _pred("Runtime_Fault", "app/wrongservice", "x"),
        _pred("Runtime_Fault", "app/alsowrong", "y"),
        _pred("Runtime_Fault", "app/paymentservice", "z"),  # rank-3 hit
    ]
    scores = score_predictions(preds, GT)
    assert scores["object_a1"] == 0.0
    assert scores["object_a3"] == 1.0


def test_object_metrics_zero_when_correct_object_absent_from_top_3() -> None:
    """Total miss on localization — agent never named the right service."""
    preds = [
        _pred("Runtime_Fault", "app/svc-a", "x"),
        _pred("Runtime_Fault", "app/svc-b", "y"),
        _pred("Runtime_Fault", "app/svc-c", "z"),
    ]
    scores = score_predictions(preds, GT)
    assert scores["object_a1"] == 0.0
    assert scores["object_a3"] == 0.0


def test_object_a1_normalizes_text_case_insensitive() -> None:
    """Both ``normalize_text(prediction.fault_object)`` and
    ``normalize_text(ground_truth.fault_object)`` lowercase + strip.
    Case / surrounding whitespace must not cost a point."""
    preds = [_pred("Runtime_Fault", "  APP/PaymentService  ", "liveness_probe_incorrect_port")]
    scores = score_predictions(preds, GT)
    assert scores["object_a1"] == 1.0


def test_score_predictions_handles_empty_input() -> None:
    """Defensive: no predictions at all (e.g., predictor LLM failure) →
    every metric stays at the dataclass default 0.0, no crash."""
    scores = score_predictions([], GT)
    assert scores["object_a1"] == 0.0
    assert scores["object_a3"] == 0.0
    assert scores["a1"] == 0.0
    assert scores["partial_a1"] == 0.0


@pytest.mark.parametrize(
    ("case_name", "predicted_object", "expected_object_a1"),
    [
        # The two failure patterns surfaced in the 11:46 analysis:
        ("trainticket_localization_miss", "app/ts-payment-service", 0.0),  # neighbor blamed
        ("boutique_localization_hit", "app/paymentservice", 1.0),  # right service
    ],
)
def test_object_a1_separates_localization_failure_from_label_failure(
    case_name: str, predicted_object: str, expected_object_a1: float
) -> None:
    """Pin the two-system failure-mode split that motivates this metric.
    Without object_a1, both rows below score a1=0 identically — the
    aggregate report would hide that one case got localization right
    and the other didn't."""
    preds = [_pred("Runtime_Fault", predicted_object, "wrong_cause")]
    scores = score_predictions(preds, GT)
    assert scores["object_a1"] == expected_object_a1, case_name
    # a1 is 0 in BOTH rows — without object_a1, the difference is invisible
    assert scores["a1"] == 0.0
