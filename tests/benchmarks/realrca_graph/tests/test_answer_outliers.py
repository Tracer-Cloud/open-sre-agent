from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.realrca_graph.answer_outliers import build_answer_outlier_report
from tests.benchmarks.realrca_graph.cli import main


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_answer_outlier_report_ranks_structural_answer_mismatch(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _write_json(
        baseline,
        {
            "results": [
                {
                    "case_id": "case-query",
                    "diagnosis_output": "根因：redis 缓存 timeout。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                },
                {
                    "case_id": "case-sql",
                    "diagnosis_output": "根因：orders 慢 SQL 全表扫描。",
                    "trace_id": "212a6a3417840231458777961e0d46",
                },
            ]
        },
    )
    internal = tmp_path / "internal.json"
    _write_json(
        internal,
        {
            "cases": [
                {
                    "case_id": "case-query",
                    "case_suffix": "query",
                    "case_type": "TDDL",
                    "matches": [{"case_id": "case-sql", "similarity": 0.8}],
                }
            ]
        },
    )
    public = tmp_path / "public.json"
    _write_json(
        public,
        {
            "cases": [
                {
                    "case_id": "case-query",
                    "case_suffix": "query",
                    "case_type": "TDDL",
                    "matches": [
                        {
                            "split": "validation",
                            "case_id": "case-public",
                            "similarity": 0.9,
                            "matched_mechanisms": ["sql"],
                        }
                    ],
                }
            ]
        },
    )
    frontier = tmp_path / "frontier.json"
    _write_json(
        frontier,
        {
            "cases": [
                {
                    "case_id": "case-query",
                    "case_suffix": "query",
                    "case_type": "TDDL",
                    "frontier_score": 3.0,
                    "bucket": "root_boundary_probe",
                    "signals": ["root_candidate_mismatch"],
                    "blockers": [],
                }
            ]
        },
    )

    report = build_answer_outlier_report(
        baseline_path=baseline,
        internal_analogue_path=internal,
        public_analogue_path=public,
        frontier_path=frontier,
    )

    case = report.cases[0]
    assert case.case_id == "case-query"
    assert "internal_answer_mechanism_outlier" in case.categories
    assert "public_graph_mechanism_outlier" in case.categories
    assert "frontier:root_boundary_probe" in case.categories
    assert case.outlier_score > 3.0


def test_answer_outlier_report_demotes_hard_negative_feedback(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _write_json(
        baseline,
        {
            "results": [
                {
                    "case_id": "case-query",
                    "diagnosis_output": "根因：redis 缓存 timeout。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                },
                {
                    "case_id": "case-sql",
                    "diagnosis_output": "根因：orders 慢 SQL 全表扫描。",
                    "trace_id": "212a6a3417840231458777961e0d46",
                },
            ]
        },
    )
    internal = tmp_path / "internal.json"
    _write_json(
        internal,
        {
            "cases": [
                {
                    "case_id": "case-query",
                    "case_suffix": "query",
                    "case_type": "TDDL",
                    "matches": [{"case_id": "case-sql", "similarity": 0.8}],
                }
            ]
        },
    )
    frontier = tmp_path / "frontier.json"
    _write_json(
        frontier,
        {
            "cases": [
                {
                    "case_id": "case-query",
                    "case_suffix": "query",
                    "case_type": "TDDL",
                    "frontier_score": 4.0,
                    "bucket": "raw_mechanism_probe",
                    "signals": ["raw_boundary_mechanism_gap"],
                    "blockers": ["known_negative_probe", "negative_tomography_variant"],
                }
            ]
        },
    )

    report = build_answer_outlier_report(
        baseline_path=baseline,
        internal_analogue_path=internal,
        frontier_path=frontier,
    )

    assert "hard_negative_feedback" in report.cases[0].categories
    assert report.cases[0].outlier_score < 2.0


def test_answer_outliers_cli_writes_report(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _write_json(
        baseline,
        {
            "results": [
                {
                    "case_id": "case-query",
                    "diagnosis_output": "根因：redis 缓存 timeout。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    out_json = tmp_path / "outliers.json"
    out_md = tmp_path / "outliers.md"

    assert (
        main(
            [
                "answer-outliers",
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
    assert payload["cases"][0]["case_id"] == "case-query"
    assert "RealRCA Answer Boundary Outliers" in out_md.read_text(encoding="utf-8")
