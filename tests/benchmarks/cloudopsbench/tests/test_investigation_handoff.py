"""Tests for B1 investigation → predictor rank-1 handoff."""

from __future__ import annotations

from unittest.mock import patch

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


def test_blocks_promotion_when_rank2_root_cause_wins_but_fault_object_wrong() -> None:
    """DB-localization class: RC tokens match via generic 'mysql' in logs, but
    investigation localized ts-order-service — do not promote tsdb-mysql."""
    predictions = [
        {
            "rank": 1,
            "fault_taxonomy": "Runtime_Fault",
            "fault_object": "app/ts-order-service",
            "root_cause": "service_dns_resolution_failure",
        },
        {
            "rank": 2,
            "fault_taxonomy": "Runtime_Fault",
            "fault_object": "app/tsdb-mysql",
            "root_cause": "mysql_invalid_credentials",
        },
    ]
    summary = (
        "Investigation conclusion (root cause): MySQL authentication errors "
        "seen from ts-order-service caller logs.\n\n"
        "Supporting RCA report:\n"
        "ts-order-service cannot reach the database; mysql access denied in "
        "caller pod logs."
    )
    aligned = align_predictions_to_investigation(predictions, summary)
    assert aligned[0]["root_cause"] == "service_dns_resolution_failure"
    assert aligned[0]["fault_object"] == "app/ts-order-service"


def test_blocks_promotion_when_db_object_only_in_logs_not_conclusion() -> None:
    """DB-object name appears only in the logs portion of the investigation
    summary, while the conclusion localizes elsewhere. The object gate must
    refuse cross-object promotion: investigation conclusion is authoritative;
    incidental log mentions of the DB service in the upstream caller's log
    lines (e.g. "connection to tsdb-mysql failed (Access denied)") are not
    enough to override the conclusion.
    """
    predictions = [
        {
            "rank": 1,
            "fault_taxonomy": "Runtime_Fault",
            "fault_object": "app/ts-order-service",
            "root_cause": "service_dns_resolution_failure",
        },
        {
            "rank": 2,
            "fault_taxonomy": "Runtime_Fault",
            "fault_object": "app/tsdb-mysql",
            "root_cause": "mysql_invalid_credentials",
        },
    ]
    summary = (
        "Identified component: ts-order-service.\n"
        "Investigation conclusion (root cause): mysql authentication failure "
        "in ts-order-service (caller side).\n\n"
        "Supporting RCA report:\n"
        "ts-order-service tried to reach tsdb-mysql; access denied for user "
        "'root' invalid credentials returned by tsdb-mysql."
    )
    aligned = align_predictions_to_investigation(predictions, summary)
    assert aligned[0]["fault_object"] == "app/ts-order-service"
    assert aligned[0]["root_cause"] == "service_dns_resolution_failure"


def test_promotes_when_investigation_names_db_fault_object() -> None:
    """When investigation localizes tsdb-mysql, cross-object promotion is allowed."""
    predictions = [
        {
            "rank": 1,
            "fault_taxonomy": "Runtime_Fault",
            "fault_object": "app/ts-order-service",
            "root_cause": "service_dns_resolution_failure",
        },
        {
            "rank": 2,
            "fault_taxonomy": "Runtime_Fault",
            "fault_object": "app/tsdb-mysql",
            "root_cause": "mysql_invalid_credentials",
        },
    ]
    summary = (
        "Investigation conclusion (root cause): invalid mysql credentials on "
        "tsdb-mysql.\n\n"
        "Supporting RCA report:\n"
        "tsdb-mysql logs: Access denied for user 'root'@'mysql' invalid credentials."
    )
    aligned = align_predictions_to_investigation(predictions, summary)
    assert aligned[0]["fault_object"] == "app/tsdb-mysql"
    assert aligned[0]["root_cause"] == "mysql_invalid_credentials"


def test_prefers_rank2_over_spurious_rank3_token_overlap() -> None:
    """Rank-3 may accumulate generic substring hits; best alt should be rank-2."""
    predictions = [
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
            "root_cause": "incorrect_image_reference",
        },
    ]
    summary = (
        "Investigation conclusion (root cause): mysql invalid credentials on "
        "ts-order-service.\n\n"
        "Supporting RCA report:\n"
        "Caller logs mention mysql access denied and invalid credentials. "
        "No image pull errors observed."
    )
    aligned = align_predictions_to_investigation(predictions, summary)
    assert aligned[0]["root_cause"] == "mysql_invalid_credentials"
    assert aligned[0]["rank"] == 1


def test_adapter_skips_handoff_for_structured_predictor_variant() -> None:
    """B1 must not layer onto structured-outputs runs (independent mechanism)."""
    from pathlib import Path
    from unittest.mock import MagicMock

    from tests.benchmarks._framework.adapters import BenchmarkCase
    from tests.benchmarks.cloudopsbench.adapter import CloudOpsBenchAdapter
    from tests.benchmarks.cloudopsbench.tests.test_predictor import _run_with_diagnosis

    adapter = CloudOpsBenchAdapter.__new__(CloudOpsBenchAdapter)
    adapter._benchmark_dir = Path("/tmp")
    adapter._predictor_variant = "structured"

    case_id = "trainticket/runtime/56"
    adapter._cases_by_id = {
        case_id: MagicMock(
            namespace="train-ticket",
            fault_category="runtime",
            case_dir=Path("/tmp"),
        )
    }

    raw_predictions = _runtime_56_style_predictions()
    run = _run_with_diagnosis(
        {
            "root_cause": "MySQL authentication failure on ts-order-service.",
            "report": "Access denied invalid credentials mysql.",
        }
    )
    case = BenchmarkCase(
        case_id=case_id,
        benchmark_name="cloudopsbench",
        system="trainticket",
        fault_category="runtime",
        metadata={},
    )

    with (
        patch.object(
            adapter,
            "build_alert",
            return_value=type("Alert", (), {"normalized": {}})(),
        ),
        patch(
            "tests.benchmarks.cloudopsbench.adapter.performance_context_for_case_dir",
            return_value=("", None),
        ),
        patch("services.agent_llm_client.get_agent_llm"),
        patch(
            "tests.benchmarks.cloudopsbench.predictor.llm_call_structured_openai.emit_paper_predictions_structured",
            return_value={"top_3_predictions": [dict(p) for p in raw_predictions]},
        ),
        patch(
            "tests.benchmarks.cloudopsbench.predictor.investigation_handoff.apply_investigation_handoff",
        ) as handoff_mock,
    ):
        result = adapter.format_final_answer(case, run, spec=None)

    handoff_mock.assert_not_called()
    assert result.final_diagnosis["top_3_predictions"][0]["root_cause"] == (
        "service_dns_resolution_failure"
    )
