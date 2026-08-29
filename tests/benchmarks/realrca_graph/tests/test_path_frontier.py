from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.path_frontier import build_path_frontier_report

CASE_ID = "01a0330f-29a8-7e83-8121-3bf4cce321aa"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _graph_context() -> dict:
    return {
        "case": {"case_id": CASE_ID, "case_type": "TDDL", "data_ref": "alarm-a"},
        "nodes": [
            {"id": f"case:{CASE_ID}", "kind": "case", "label": CASE_ID, "props": {}},
            {"id": "alarm:a", "kind": "alarm", "label": "alarm-a", "props": {}},
            {"id": "trace:t1", "kind": "trace", "label": "t1", "props": {}},
            {"id": "span:s1", "kind": "span", "label": "orders slow sql", "props": {}},
            {"id": "service:orders", "kind": "service", "label": "orders slow sql", "props": {}},
        ],
        "edges": [
            {"source": f"case:{CASE_ID}", "rel": "HAS_ALARM", "target": "alarm:a"},
            {"source": "alarm:a", "rel": "MENTIONS", "target": "trace:t1"},
            {"source": "trace:t1", "rel": "HAS_SPAN", "target": "span:s1"},
            {"source": "span:s1", "rel": "INVOKES", "target": "service:orders"},
        ],
        "evidence": [
            {
                "name": "trace_get",
                "summary": "trace spans=1 sql_top=service=TDDL_QUERY@db:orders duration_ms=3000",
                "command": "sf trace get t1",
            }
        ],
        "root_candidates": [
            {
                "kind": "trace_span",
                "label": "orders slow sql",
                "score": 5.0,
                "reason": "abnormal trace span",
                "props": {"trace_id": "t1", "service": "orders slow sql", "duration_ms": 3000},
            }
        ],
        "retrieval_summary": "",
    }


def test_path_frontier_report_scores_current_answer_path(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _write_json(
        baseline,
        {
            "results": [
                {
                    "case_id": CASE_ID,
                    "diagnosis_output": "根因：orders slow sql 导致接口超时。",
                    "trace_id": "t1",
                }
            ]
        },
    )
    graph_root = tmp_path / "graphs"
    _write_json(graph_root / "test" / CASE_ID / "graph_context.json", _graph_context())

    report = build_path_frontier_report(baseline_path=baseline, graph_roots=[graph_root])

    assert report.case_count == 1
    assert report.cases[0].case_suffix == "21aa"
    assert "strong_symptom_path" in report.cases[0].categories
    assert report.cases[0].path_score is not None


def test_path_frontier_cli_writes_report(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _write_json(
        baseline,
        {
            "results": [
                {
                    "case_id": CASE_ID,
                    "diagnosis_output": "根因：orders slow sql 导致接口超时。",
                    "trace_id": "t1",
                }
            ]
        },
    )
    graph_root = tmp_path / "graphs"
    _write_json(graph_root / "test" / CASE_ID / "graph_context.json", _graph_context())
    out_json = tmp_path / "path-frontier.json"
    out_md = tmp_path / "path-frontier.md"

    assert (
        main(
            [
                "path-frontier",
                "--graph-root",
                str(graph_root),
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
    assert payload["case_count"] == 1
    assert "RealRCA Path Frontier" in out_md.read_text(encoding="utf-8")
