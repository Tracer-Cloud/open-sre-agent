from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import tests.synthetic.rds_postgres.run_suite as run_suite_module
from tests.synthetic.rds_postgres.scenario_loader import (
    SUITE_DIR,
    GoldenTrajectoryConfig,
    load_scenario,
)


def _fake_final_state() -> dict[str, Any]:
    return {
        "root_cause": "Replication lag due to write pressure.",
        "root_cause_category": "replication_lag",
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "evidence": {
            "aws_cloudwatch_metrics": {
                "db_instance_identifier": "payments-prod",
                "metrics": [{"metric_name": "CPUUtilization"}],
                "observations": ["CPU elevated"],
            }
        },
        "executed_hypotheses": [
            {"actions": ["query_grafana_metrics"], "failed_actions": []},
        ],
        "investigation_loop_count": 1,
    }


def _fake_final_state_with_two_loops() -> dict[str, Any]:
    return {
        "root_cause": "Replication lag due to write pressure.",
        "root_cause_category": "replication_lag",
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "evidence": {
            "aws_cloudwatch_metrics": {
                "db_instance_identifier": "payments-prod",
                "metrics": [{"metric_name": "CPUUtilization"}],
                "observations": ["CPU elevated"],
            }
        },
        "executed_hypotheses": [
            {"actions": ["query_grafana_metrics"], "failed_actions": []},
            {"actions": ["query_grafana_logs"], "failed_actions": []},
        ],
        "investigation_loop_count": 2,
    }


def _fake_write_observation(
    _observation: Any,
    observations_dir: Path,
) -> Path:
    target = observations_dir / "001-replication-lag" / "latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    return target


def test_run_suite_applies_trajectory_policy_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fixture = load_scenario(SUITE_DIR / "001-replication-lag")
    fixture = replace(
        fixture,
        answer_key=replace(
            fixture.answer_key,
            golden_trajectory=GoldenTrajectoryConfig(
                ordered_actions=[
                    "query_grafana_metrics",
                    "query_grafana_logs",
                ],
                matching="strict",
                max_extra_actions=0,
            ),
        ),
    )

    score = run_suite_module.ScenarioScore(
        scenario_id=fixture.scenario_id,
        passed=True,
        root_cause_present=True,
        expected_category=fixture.answer_key.root_cause_category,
        actual_category=fixture.answer_key.root_cause_category,
        missing_keywords=[],
        matched_keywords=list(fixture.answer_key.required_keywords),
        root_cause="Replication lag due to write pressure.",
    )

    render_calls: list[str] = []

    monkeypatch.setattr(run_suite_module, "load_all_scenarios", lambda _suite_dir: [fixture])
    monkeypatch.setattr(
        run_suite_module, "run_scenario", lambda *_args, **_kwargs: (_fake_final_state(), score)
    )
    monkeypatch.setattr(run_suite_module, "write_observation", _fake_write_observation)
    monkeypatch.setattr(
        run_suite_module,
        "render_report_to_console",
        lambda *_args, **_kwargs: render_calls.append("called"),
    )

    results = run_suite_module.run_suite(
        [
            "--scenario",
            fixture.scenario_id,
            "--report",
            "--observations-dir",
            str(tmp_path),
        ]
    )

    assert len(results) == 1
    assert results[0].passed is False
    assert "trajectory policy failed" in results[0].failure_reason
    assert render_calls == ["called"]


def test_run_suite_enforces_resolved_loop_threshold_and_persists_score_consistency(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fixture = load_scenario(SUITE_DIR / "001-replication-lag")
    fixture = replace(
        fixture,
        answer_key=replace(
            fixture.answer_key,
            max_investigation_loops=1,
            golden_trajectory=GoldenTrajectoryConfig(
                ordered_actions=["query_grafana_metrics"],
                matching="set",
            ),
        ),
    )

    score = run_suite_module.ScenarioScore(
        scenario_id=fixture.scenario_id,
        passed=True,
        root_cause_present=True,
        expected_category=fixture.answer_key.root_cause_category,
        actual_category=fixture.answer_key.root_cause_category,
        missing_keywords=[],
        matched_keywords=list(fixture.answer_key.required_keywords),
        root_cause="Replication lag due to write pressure.",
    )

    persisted_scores: list[dict[str, Any]] = []

    def _capture_write_observation(observation: Any, observations_dir: Path) -> Path:
        persisted_scores.append(dict(observation.score))
        target = observations_dir / "001-replication-lag" / "latest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
        return target

    monkeypatch.setattr(run_suite_module, "load_all_scenarios", lambda _suite_dir: [fixture])
    monkeypatch.setattr(
        run_suite_module,
        "run_scenario",
        lambda *_args, **_kwargs: (_fake_final_state_with_two_loops(), score),
    )
    monkeypatch.setattr(run_suite_module, "write_observation", _capture_write_observation)

    results = run_suite_module.run_suite(
        [
            "--scenario",
            fixture.scenario_id,
            "--observations-dir",
            str(tmp_path),
        ]
    )

    assert len(results) == 1
    assert results[0].passed is False
    assert "trajectory policy failed" in results[0].failure_reason
    assert "loops_used=2 > 1" in results[0].failure_reason
    assert len(persisted_scores) == 1
    assert persisted_scores[0]["passed"] is False
    assert persisted_scores[0]["failure_reason"] == results[0].failure_reason


def test_run_suite_json_mode_suppresses_report_render(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fixture = load_scenario(SUITE_DIR / "001-replication-lag")

    score = run_suite_module.ScenarioScore(
        scenario_id=fixture.scenario_id,
        passed=True,
        root_cause_present=True,
        expected_category=fixture.answer_key.root_cause_category,
        actual_category=fixture.answer_key.root_cause_category,
        missing_keywords=[],
        matched_keywords=list(fixture.answer_key.required_keywords),
        root_cause="Replication lag due to write pressure.",
    )

    render_calls: list[str] = []

    monkeypatch.setattr(run_suite_module, "load_all_scenarios", lambda _suite_dir: [fixture])
    monkeypatch.setattr(
        run_suite_module, "run_scenario", lambda *_args, **_kwargs: (_fake_final_state(), score)
    )
    monkeypatch.setattr(run_suite_module, "write_observation", _fake_write_observation)
    monkeypatch.setattr(
        run_suite_module,
        "render_report_to_console",
        lambda *_args, **_kwargs: render_calls.append("called"),
    )

    _ = run_suite_module.run_suite(
        [
            "--scenario",
            fixture.scenario_id,
            "--report",
            "--json",
            "--observations-dir",
            str(tmp_path),
        ]
    )

    assert render_calls == []
