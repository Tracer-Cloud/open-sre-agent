from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.score_boundaries import build_score_boundary_report


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _baseline(path: Path) -> None:
    _write_json(
        path,
        {
            "results": [
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321aa",
                    "diagnosis_output": "根因：HSF provider 线程池打满。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                },
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321bb",
                    "diagnosis_output": "根因：Redis timeout。",
                    "trace_id": "212a6a3417840231458777961e0d46",
                },
            ]
        },
    )


def test_score_boundary_report_prefers_zero_delta_uncertain_case(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _baseline(baseline)
    frontier = tmp_path / "frontier.json"
    _write_json(
        frontier,
        {
            "cases": [
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321aa",
                    "case_suffix": "21aa",
                    "case_type": "HSF",
                    "bucket": "root_boundary_probe",
                    "frontier_score": 4.2,
                    "signals": ["root_candidate_mismatch"],
                    "blockers": [],
                    "baseline_support": 0.82,
                },
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321bb",
                    "case_suffix": "21bb",
                    "case_type": "Tair",
                    "bucket": "raw_mechanism_probe",
                    "frontier_score": 4.0,
                    "signals": ["raw_boundary_mechanism_gap"],
                    "blockers": ["large_negative_probe_delta"],
                    "baseline_support": 1.0,
                },
            ]
        },
    )
    tomography = tmp_path / "tomography.json"
    _write_json(
        tomography,
        {
            "cases": [
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321aa",
                    "case_suffix": "21aa",
                    "best_estimate": 0.0,
                    "estimates": [
                        {
                            "estimate": 0.0,
                            "observation_count": 1,
                            "methods": ["constraint_single_unknown"],
                        }
                    ],
                },
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321bb",
                    "case_suffix": "21bb",
                    "best_estimate": -1.01,
                    "estimates": [
                        {
                            "estimate": -1.01,
                            "observation_count": 1,
                            "methods": ["direct_single_case"],
                        }
                    ],
                },
            ]
        },
    )

    report = build_score_boundary_report(
        baseline_path=baseline,
        frontier_path=frontier,
        tomography_path=tomography,
    )

    assert report.cases[0].case_suffix == "21aa"
    assert report.cases[0].action == "generate_boundary_challenger"
    assert "zero_delta_uncertain" in report.cases[0].categories
    assert report.cases[1].action == "avoid"


def test_score_boundary_report_promotes_unobserved_frontier_case(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _baseline(baseline)
    frontier = tmp_path / "frontier.json"
    _write_json(
        frontier,
        {
            "cases": [
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321aa",
                    "case_suffix": "21aa",
                    "case_type": "HSF",
                    "bucket": "raw_mechanism_probe",
                    "frontier_score": 2.5,
                    "signals": ["raw_boundary_mechanism_gap"],
                    "blockers": [],
                    "baseline_support": 0.7,
                }
            ]
        },
    )

    report = build_score_boundary_report(baseline_path=baseline, frontier_path=frontier)

    assert report.cases[0].case_suffix == "21aa"
    assert report.cases[0].action in {"generate_candidate", "generate_boundary_challenger"}
    assert "no_public_variant_feedback" in report.cases[0].categories


def test_score_boundary_promotes_unobserved_root_changing_raw_gap(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _baseline(baseline)
    frontier = tmp_path / "frontier.json"
    _write_json(
        frontier,
        {
            "cases": [
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321aa",
                    "case_suffix": "21aa",
                    "case_type": "TDDL",
                    "bucket": "raw_mechanism_probe",
                    "frontier_score": 1.8,
                    "signals": ["raw_boundary_mechanism_gap"],
                    "blockers": [],
                    "baseline_support": 1.0,
                    "raw_score": 6.0,
                    "raw_uncovered_mechanisms": ["timeout"],
                }
            ]
        },
    )
    outlier = tmp_path / "outlier.json"
    _write_json(
        outlier,
        {
            "cases": [
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321aa",
                    "case_suffix": "21aa",
                    "case_type": "TDDL",
                    "outlier_score": 0.6,
                    "categories": ["frontier:raw_mechanism_probe"],
                }
            ]
        },
    )

    report = build_score_boundary_report(
        baseline_path=baseline,
        frontier_path=frontier,
        answer_outlier_path=outlier,
    )

    case = next(item for item in report.cases if item.case_suffix == "21aa")
    assert case.action == "generate_boundary_challenger"
    assert "root_changing_raw_gap" in case.categories
    assert "stable_low_information_gap" not in case.categories


def test_score_boundary_softens_negative_history_for_untried_zero_delta_mechanism(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.json"
    _baseline(baseline)
    frontier = tmp_path / "frontier.json"
    _write_json(
        frontier,
        {
            "cases": [
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321aa",
                    "case_suffix": "21aa",
                    "case_type": "TDDL",
                    "bucket": "raw_mechanism_probe",
                    "frontier_score": 3.9,
                    "signals": [
                        "root_candidate_mismatch",
                        "raw_boundary_mechanism_gap",
                        "untried_root_mechanism_after_negative",
                    ],
                    "blockers": ["case_negative_probe_history", "large_negative_probe_delta"],
                    "baseline_support": 1.0,
                    "raw_score": 5.5,
                    "raw_uncovered_mechanisms": ["slow_sql"],
                }
            ]
        },
    )
    tomography = tmp_path / "tomography.json"
    _write_json(
        tomography,
        {
            "cases": [
                {
                    "case_id": "01a0330f-29a8-7e83-8121-3bf4cce321aa",
                    "case_suffix": "21aa",
                    "best_estimate": 0.0,
                    "estimates": [
                        {
                            "estimate": 0.0,
                            "observation_count": 1,
                            "methods": ["constraint_single_unknown"],
                        }
                    ],
                }
            ]
        },
    )

    report = build_score_boundary_report(
        baseline_path=baseline,
        frontier_path=frontier,
        tomography_path=tomography,
    )

    case = next(item for item in report.cases if item.case_suffix == "21aa")
    assert case.action == "generate_boundary_challenger"
    assert "soft_negative_history_untried_mechanism" in case.categories
    assert "hard_public_feedback_blocker" not in case.categories


def test_score_boundaries_cli_writes_report(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _baseline(baseline)
    out_json = tmp_path / "boundaries.json"
    out_md = tmp_path / "boundaries.md"

    assert (
        main(
            [
                "score-boundaries",
                "--baseline",
                str(baseline),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["case_count"] == 2
    assert "RealRCA Score Boundary Report" in out_md.read_text(encoding="utf-8")
