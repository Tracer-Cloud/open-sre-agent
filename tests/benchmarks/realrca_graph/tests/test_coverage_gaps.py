from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.coverage_gaps import (
    build_coverage_gap_report,
    render_coverage_gap_markdown,
)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_coverage_gap_report_marks_missing_sql_for_tddl(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    graph_root = tmp_path / "graphs"
    _write_json(
        tmp_path / "dataset" / "test.json",
        [{"case_id": case_id, "type": "TDDL"}],
    )
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：order-service 访问 TDDL 数据库变慢。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "TDDL"},
            "root_candidates": [
                {
                    "kind": "metric_series",
                    "label": "middleware_tddl_read_qps:app_group=order-service",
                    "score": 4.4,
                    "reason": "TDDL read metric changed near alarm",
                }
            ],
            "evidence": [
                {
                    "name": "metric_middleware_tddl_read_qps",
                    "command": "sf metric query middleware_tddl_read_qps -f json",
                    "returncode": 0,
                    "summary": "order-service TDDL read qps changed near alarm",
                }
            ],
        },
    )

    report = build_coverage_gap_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "dataset",
    )

    case = report.cases[0]
    assert case.case_id == case_id
    assert "missing_modality:sql" in case.categories
    assert case.missing_modalities == ["sql"]
    assert "补 TDDL/RDS SQL 证据" in case.recommended_actions[0]


def test_coverage_gap_report_marks_known_negative_probe(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321bb"
    graph_root = tmp_path / "graphs"
    _write_json(
        tmp_path / "dataset" / "test.json",
        [{"case_id": case_id, "type": "HSF"}],
    )
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：provider-app HSF 调用超时。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        tmp_path / "leaderboard.json",
        {
            "items": [
                {"team_name": "隐元玩一玩", "agent_name": "probe-gselect-21f8", "accuracy": 84.85},
                {"team_name": "隐元玩一玩", "agent_name": "probe-similar-21bb", "accuracy": 81.82},
            ]
        },
    )
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app:provider_group",
                    "score": 5.0,
                    "reason": "provider timeout",
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                    "returncode": 0,
                    "summary": "provider-app timeout",
                },
                {
                    "name": "metric_hsf_rt",
                    "command": "sf metric query middleware_hsf_provider_service_method_rt -f json",
                    "returncode": 0,
                    "summary": "provider-app RT rose",
                },
            ],
        },
    )

    report = build_coverage_gap_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "dataset",
        leaderboard_path=tmp_path / "leaderboard.json",
    )

    case = report.cases[0]
    assert "known_negative_probe" in case.categories
    assert any("不要重复文本扩写" in action for action in case.recommended_actions)


def test_coverage_gap_report_counts_hypothesis_modality_as_coverage(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321cc"
    graph_root = tmp_path / "graphs"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：resource_lock_setting_his 慢 SQL。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "TDDL"},
            "root_candidates": [
                {
                    "kind": "pattern_slow_sql",
                    "label": "resource_lock_setting_his",
                    "score": 5.0,
                    "reason": "visible SQL evidence indicates TDDL_QUERY on resource_lock_setting_his",
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                    "returncode": 0,
                    "summary": "TDDL_QUERY@intl_bw:resource_lock_setting_his duration=2646ms",
                }
            ],
        },
    )

    report = build_coverage_gap_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "sql" in case.top_hypothesis_modalities
    assert "missing_modality:sql" not in case.categories
    assert case.missing_modalities == ["metric"]


def test_render_coverage_gap_markdown_and_cli_write_outputs(tmp_path) -> None:
    case_id = "case-a"
    graph_root = tmp_path / "graphs"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：sql slow.",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "TDDL"},
            "root_candidates": [],
            "evidence": [],
        },
    )
    out_json = tmp_path / "gaps.json"
    out_md = tmp_path / "gaps.md"

    assert (
        main(
            [
                "coverage-gaps",
                "--baseline",
                str(tmp_path / "baseline.json"),
                "--graph-root",
                str(graph_root),
                "--dataset-dir",
                str(tmp_path / "missing-dataset"),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    markdown = render_coverage_gap_markdown(
        build_coverage_gap_report(
            baseline_path=tmp_path / "baseline.json",
            graph_roots=[graph_root],
            split="test",
            dataset_dir=tmp_path / "missing-dataset",
        )
    )

    assert payload["cases"][0]["case_id"] == case_id
    assert "RealRCA Coverage Gaps" in out_md.read_text(encoding="utf-8")
    assert "missing_root_hypothesis" in markdown
