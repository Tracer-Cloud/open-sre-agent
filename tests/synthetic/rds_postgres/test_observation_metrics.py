from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.synthetic.rds_postgres.observations import (
    TrajectoryPolicy,
    build_observation,
    compute_trajectory_metrics,
    edit_distance,
    evaluate_trajectory_policy,
    lcs_length,
    render_report_to_string,
    write_observation,
)
from tests.synthetic.rds_postgres.scenario_loader import load_all_scenarios


def _sample_final_state() -> dict[str, Any]:
    return {
        "evidence": {
            "grafana_metrics": [{"metric_name": "CPUUtilization"}],
            "grafana_logs": [{"message": "replica lag detected"}],
            "aws_cloudwatch_metrics": {
                "db_instance_identifier": "db-1",
                "metrics": [{"metric_name": "CPUUtilization"}],
                "observations": ["CPU is elevated"],
            },
            "aws_performance_insights": {
                "observations": ["Top SQL Activity: select 1 | Avg Load: 2.0 AAS | Waits: CPU"],
                "top_sql": [{"sql": "select 1", "db_load": 2.0, "wait_event": "CPU"}],
                "wait_events": [],
            },
        },
        "executed_hypotheses": [
            {"actions": ["query_grafana_metrics", "query_grafana_logs"], "failed_actions": []}
        ],
        "investigation_loop_count": 1,
        "root_cause": "Replication lag from write-heavy workload.",
    }


def _sample_score_payload() -> dict[str, Any]:
    return {
        "scenario_id": "001-replication-lag",
        "passed": True,
        "expected_category": "resource_exhaustion",
        "actual_category": "resource_exhaustion",
        "missing_keywords": [],
        "matched_keywords": ["replication lag", "wal"],
        "exact_matched_keywords": ["replication lag", "wal"],
        "exact_missing_keywords": [],
        "semantic_matched_keywords": ["replication lag", "wal"],
        "semantic_missing_keywords": [],
        "exact_keyword_match": True,
        "semantic_keyword_match": True,
        "normalization_used": ["casefold_whitespace_normalization", "exact_phrase"],
        "gates": {
            "category_match": {
                "status": "pass",
                "threshold": "actual_category == 'resource_exhaustion'",
                "actual": "root_cause_present=True, actual_category='resource_exhaustion'",
            },
            "required_keyword_match": {
                "status": "pass",
                "threshold": "all required keywords matched (semantic)",
                "actual": "missing_semantic=[], missing_exact=[]",
            },
            "required_evidence_sources": {
                "status": "pass",
                "threshold": "all required evidence sources populated",
                "actual": "missing_required_evidence=[]",
            },
            "trajectory_budget": {
                "status": "pass",
                "threshold": "extra_actions_count == 0",
                "actual": "extra_actions_count=0",
            },
            "forbidden_category_clear": {
                "status": "pass",
                "threshold": "actual_category not in forbidden_categories",
                "actual": "actual_category='resource_exhaustion', forbidden=[]",
            },
            "forbidden_keyword_clear": {
                "status": "pass",
                "threshold": "no forbidden keywords appear in graded output text",
                "actual": "forbidden_hits=[]",
            },
            "failover_event_reasoning": {
                "status": "pass",
                "threshold": "not required unless failover sequence keywords are in answer key",
                "actual": "not_applicable",
            },
        },
        "failure_reasons": [],
        "failure_reason": "",
        "trajectory": {
            "expected_sequence": [
                "query_grafana_metrics",
                "query_grafana_logs",
                "query_grafana_alert_rules",
            ]
        },
    }


def test_lcs_length_and_edit_distance() -> None:
    a = ["query_grafana_metrics", "query_grafana_logs", "query_grafana_alert_rules"]
    b = ["query_grafana_metrics", "query_grafana_alert_rules"]
    assert lcs_length(a, b) == 2
    assert edit_distance(a, b) == 1


def test_compute_trajectory_metrics_detects_missing_and_redundancy() -> None:
    executed = [
        {
            "actions": [
                "query_grafana_metrics",
                "query_grafana_metrics",
                "query_grafana_logs",
            ],
            "failed_actions": [],
        }
    ]
    golden = [
        "query_grafana_metrics",
        "query_grafana_logs",
        "query_grafana_alert_rules",
    ]
    metrics = compute_trajectory_metrics(
        executed_hypotheses=executed,
        golden=golden,
        loops_used=1,
        max_loops=4,
    )
    assert metrics.missing_actions == ["query_grafana_alert_rules"]
    assert metrics.extra_actions == []
    assert metrics.redundancy_count == 1
    assert metrics.failed_action_count == 0
    assert metrics.loop_calibration_ok is True


def test_observation_roundtrip_and_report_rendering(tmp_path: Path) -> None:
    final_state = _sample_final_state()
    score = _sample_score_payload()
    trajectory = compute_trajectory_metrics(
        executed_hypotheses=final_state["executed_hypotheses"],
        golden=score["trajectory"]["expected_sequence"],
        loops_used=1,
        max_loops=4,
    )
    observation = build_observation(
        scenario_id="001-replication-lag",
        suite="axis1",
        backend="FixtureGrafanaBackend",
        score=score,
        reasoning=None,
        trajectory=trajectory,
        evaluated_golden_actions=list(score["trajectory"]["expected_sequence"]),
        trajectory_policy=evaluate_trajectory_policy(
            metrics=trajectory,
            golden_actions=list(score["trajectory"]["expected_sequence"]),
            policy=TrajectoryPolicy(matching="lcs"),
        ),
        final_state=final_state,
        available_evidence_sources=[
            "aws_cloudwatch_metrics",
            "aws_performance_insights",
            "aws_rds_events",
        ],
        required_evidence_sources=["aws_performance_insights", "aws_rds_events"],
        started_at=datetime.now(UTC),
        wall_time_s=1.2,
    )

    output_path = write_observation(observation, tmp_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["scenario_id"] == "001-replication-lag"
    assert payload["report_schema_version"] == "report_v2"
    assert payload["scoring_formula_version"] == "v2_gated_semantic"
    assert "process_metrics" in payload["score"]
    assert payload["score"]["process_metrics"]["redundancy_count"] == 0
    assert payload["score"]["process_metrics"]["loop_count_consistent"] is True
    assert (
        "Duplicate action executions"
        in payload["score"]["process_metrics"]["definitions"]["redundancy_count"]
    )
    assert payload["score"]["actual_category"] == "resource_exhaustion"
    assert payload["observed_evidence_sources"] == [
        "aws_cloudwatch_metrics",
        "aws_performance_insights",
    ]
    assert payload["missing_required_evidence_sources"] == ["aws_rds_events"]
    assert payload["evidence_source_coverage"]["required_coverage"] == 0.5
    assert payload["evidence_source_coverage"]["available_coverage"] == 2 / 3
    assert payload["evidence_source_coverage"]["source_presence"] == {
        "aws_cloudwatch_metrics": True,
        "aws_performance_insights": True,
        "aws_rds_events": False,
    }
    assert payload["canonical_report_payload"]["status"] == "pass"
    assert payload["canonical_report_payload"]["report_schema_version"] == "report_v2"
    assert payload["canonical_report_payload"]["scoring_formula_version"] == "v2_gated_semantic"
    assert "failure_reason" not in payload["canonical_report_payload"]
    assert payload["canonical_report_payload"]["failure_reasons"] == []
    assert payload["canonical_report_payload"]["trajectory"]["golden"] == [
        "query_grafana_metrics",
        "query_grafana_logs",
        "query_grafana_alert_rules",
    ]
    assert payload["canonical_report_payload"]["trajectory"]["policy"] == {
        "passed": False,
        "matching": "lcs",
        "violations": ["lcs_ratio=0.67 < 1.00"],
    }
    assert payload["canonical_report_payload"]["evidence"]["missing_required_sources"] == [
        "aws_rds_events"
    ]
    assert payload["canonical_report_payload"]["observation_path"] == payload["observation_path"]
    assert payload["reasoning_status"] == "not_captured"
    assert "reasoning" not in payload
    assert payload["trajectory_policy_version"] == "default_v1"
    assert (tmp_path / "001-replication-lag" / "latest.json").exists()

    report_text = render_report_to_string(observation)
    assert "Synthetic RDS Run - 001-replication-lag" in report_text
    assert "PASS" in report_text
    assert "Observed evidence" in report_text
    assert "aws_performance_insights" in report_text
    assert "Missing evidence" in report_text
    assert "policy" in report_text
    assert "Trajectory" in report_text
    assert "lcs=0.67" in report_text
    assert "Observation:" in report_text


def test_compute_trajectory_metrics_handles_all_rds_scenarios() -> None:
    fixtures = load_all_scenarios()
    for fixture in fixtures:
        metrics = compute_trajectory_metrics(
            executed_hypotheses=[],
            golden=list(fixture.answer_key.optimal_trajectory),
            loops_used=0,
            max_loops=fixture.answer_key.max_investigation_loops,
        )
        assert metrics.loops_used == 0
        assert metrics.actions_per_loop == []
