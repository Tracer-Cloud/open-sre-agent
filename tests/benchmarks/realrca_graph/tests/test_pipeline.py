from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.pipeline import build_pipeline_status


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_pipeline_status_summarizes_current_best_and_next_action(tmp_path: Path) -> None:
    leaderboard = tmp_path / "leaderboard.json"
    _write_json(
        leaderboard,
        {
            "items": [
                {
                    "team_name": "隐元玩一玩",
                    "agent_name": "probe-gselect-21f8",
                    "accuracy": 84.85,
                    "coverage": 84.02,
                    "quality_score": 4.96,
                    "submitted_at": "2026-08-28T11:08:40.871000Z",
                    "model_name": "dma/deepseek-v4-pro+graph-selector-single-probe",
                },
                {
                    "team_name": "隐元玩一玩",
                    "agent_name": "probe-trajpatch-1d83",
                    "accuracy": 81.82,
                },
            ]
        },
    )
    selector = tmp_path / "selector.json"
    _write_json(
        selector,
        {
            "case_count": 99,
            "selected_case_count": 99,
            "candidate_files": ["candidate.json"],
            "accepted_replacements": [],
            "decisions": [
                {
                    "baseline_source": "baseline",
                    "scores": [
                        {"source": "baseline", "risk_flags": []},
                        {
                            "source": "candidate",
                            "risk_flags": [
                                "unsupported_high_novelty",
                                "rewrite_drops_baseline_context",
                            ],
                        },
                    ],
                }
            ],
        },
    )
    score_boundary = tmp_path / "boundaries.json"
    _write_json(
        score_boundary,
        {
            "case_count": 99,
            "action_counts": {"preserve_current_best": 4, "avoid": 95},
            "cases": [
                {
                    "case_suffix": "1d90",
                    "action": "preserve_current_best",
                    "priority_score": 1.071,
                    "categories": ["zero_delta_uncertain"],
                }
            ],
        },
    )
    tomography = tmp_path / "tomography.json"
    _write_json(
        tomography,
        {
            "reference_accuracy": 84.85,
            "matched_submission_count": 213,
            "inferred_answer_count": 171,
            "positive_answer_count": 0,
            "cases": [
                {
                    "case_id": "case-1",
                    "estimates": [
                        {"estimate": -1.01},
                        {"estimate": 0.0},
                    ],
                }
            ],
        },
    )

    status = build_pipeline_status(
        leaderboard_path=leaderboard,
        team_name="隐元玩一玩",
        selector_audit_path=selector,
        score_boundary_path=score_boundary,
        tomography_path=tomography,
        target_accuracy=90.0,
    )

    assert status.current_best.accuracy == 84.85
    assert status.score_gap == 5.15
    assert status.ready_to_submit is False
    assert status.selector_summary["top_candidate_risks"]["unsupported_high_novelty"] == 1
    assert status.tomography_summary["negative_estimate_count"] == 1
    assert (
        status.next_action
        == "mine_new_observability_sources_or_root_boundary_evidence_before_more_dma"
    )


def test_pipeline_status_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    leaderboard = tmp_path / "leaderboard.json"
    _write_json(
        leaderboard,
        {
            "items": [
                {
                    "team_name": "隐元玩一玩",
                    "agent_name": "probe-gselect-21f8",
                    "accuracy": 90.91,
                }
            ]
        },
    )
    out_json = tmp_path / "status.json"
    out_md = tmp_path / "status.md"

    assert (
        main(
            [
                "pipeline-status",
                "--leaderboard",
                str(leaderboard),
                "--target-accuracy",
                "90",
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["current_best"]["accuracy"] == 90.91
    assert payload["next_action"] == "write_yuque_report_and_freeze_successful_pipeline"
    assert "RealRCA Graph Pipeline Status" in out_md.read_text(encoding="utf-8")
