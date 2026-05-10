from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.synthetic.rds_postgres.observations import (
    build_observation,
    compute_trajectory_metrics,
    edit_distance,
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
        final_state=final_state,
        started_at=datetime.now(UTC),
        wall_time_s=1.2,
    )

    output_path = write_observation(observation, tmp_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["scenario_id"] == "001-replication-lag"
    assert payload["score"]["actual_category"] == "resource_exhaustion"
    assert (tmp_path / "001-replication-lag" / "latest.json").exists()

    report_text = render_report_to_string(observation)
    assert "Synthetic RDS Run - 001-replication-lag" in report_text
    assert "PASS" in report_text
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
