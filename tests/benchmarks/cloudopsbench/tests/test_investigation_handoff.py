"""Tests for B1 investigation → predictor rank-1 handoff."""

from __future__ import annotations

from tests.benchmarks.cloudopsbench.predictor.investigation_handoff import (
    align_predictions_to_investigation,
    apply_investigation_handoff,
)


def _runtime_56_style_predictions() -> list[dict]:
    """Predictor rank-1 DNS; rank-2 MySQL auth — investigation supports MySQL."""
    return [
        {
            "rank": 1,
            "fault_taxonomy": "Runtime_Fault",
            "fault_object": "app/ts-order-service",
            "root_cause": "service_dns_resolution_failure",
        },
        {
            "rank": 2,
            "fault_taxonomy": "Runtime_Fault",
            "fault_object": "app/ts-order-service",
            "root_cause": "mysql_invalid_credentials",
        },
        {
            "rank": 3,
            "fault_taxonomy": "Startup_Fault",
            "fault_object": "app/ts-order-service",
            "root_cause": "image_pull_failure",
        },
    ]


def test_promotes_better_evidenced_rank2_over_partial_rank1() -> None:
    """runtime/56 class: investigation names MySQL auth, predictor hedged DNS."""
    summary = (
        "Investigation conclusion (root cause): MySQL authentication failure "
        "due to invalid credentials in ts-order-service.\n\n"
        "Supporting RCA report:\n"
        "Logs show Access denied for user 'root'@'mysql' (using password: YES). "
        "Database connectivity failed after credential mismatch."
    )
    predictions = _runtime_56_style_predictions()
    aligned = align_predictions_to_investigation(predictions, summary)

    assert aligned[0]["root_cause"] == "mysql_invalid_credentials"
    assert aligned[0]["rank"] == 1
    assert aligned[1]["root_cause"] == "service_dns_resolution_failure"
    assert aligned[1]["rank"] == 2
    # Input list unchanged
    assert predictions[0]["root_cause"] == "service_dns_resolution_failure"


def test_no_change_when_summary_empty() -> None:
    predictions = _runtime_56_style_predictions()
    aligned = align_predictions_to_investigation(predictions, "")
    assert aligned == predictions


def test_no_change_when_rank1_already_best_supported() -> None:
    predictions = _runtime_56_style_predictions()
    summary = (
        "Investigation conclusion (root cause): DNS resolution failure for "
        "ts-order-service upstream dependencies."
    )
    aligned = align_predictions_to_investigation(predictions, summary)
    assert aligned[0]["root_cause"] == "service_dns_resolution_failure"


def test_apply_investigation_handoff_skips_empty_summary() -> None:
    predictions = _runtime_56_style_predictions()
    result = apply_investigation_handoff(predictions, "")
    assert result == predictions


def test_apply_investigation_handoff_runs_b1_then_conservative_rerank() -> None:
    summary = (
        "Investigation conclusion (root cause): MySQL authentication failure.\n"
        "Logs: Access denied for user 'root'@'mysql' invalid credentials."
    )
    predictions = _runtime_56_style_predictions()
    result = apply_investigation_handoff(predictions, summary)
    assert result[0]["root_cause"] == "mysql_invalid_credentials"
