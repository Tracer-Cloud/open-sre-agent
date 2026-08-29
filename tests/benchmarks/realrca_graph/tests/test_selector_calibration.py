from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.selector_calibration import (
    build_selector_calibration_report,
    render_selector_calibration_markdown,
)


def test_selector_calibration_flags_graph_overtrust_and_undertrust(tmp_path) -> None:
    paths = _write_selector_calibration_fixture(tmp_path)

    report = build_selector_calibration_report(
        result_paths=[paths["wrong_result"], paths["right_result"]],
        graph_roots=[paths["graph_root"]],
        dataset_dir=paths["dataset_dir"],
    )

    payload = report.to_dict()
    assert payload["public_validation_truth_used"] is True
    assert payload["hidden_test_reference_used"] is False
    assert report.category_counts["graph_high_answer_low"] == 1
    assert report.category_counts["answer_high_graph_low"] == 1

    case = report.cases[0]
    assert case.best_by_graph_source == "wrong"
    assert case.best_by_validation_source == "right"
    assert case.candidates[0].source == "wrong"
    assert "graph_strong_but_critical_missing" in case.candidates[0].categories

    markdown = render_selector_calibration_markdown(report)
    assert "graph_high_answer_low" in markdown
    assert "Source Summary" in markdown


def test_selector_calibration_cli_writes_report(tmp_path) -> None:
    paths = _write_selector_calibration_fixture(tmp_path)
    out_json = tmp_path / "selector.json"
    out_md = tmp_path / "selector.md"

    assert (
        main(
            [
                "selector-calibration",
                "--graph-root",
                str(paths["graph_root"]),
                "--candidate",
                str(paths["wrong_result"]),
                "--candidate",
                str(paths["right_result"]),
                "--dataset-dir",
                str(paths["dataset_dir"]),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["category_counts"]["graph_high_answer_low"] == 1
    assert out_md.read_text(encoding="utf-8").startswith("# RealRCA Selector Calibration")


def _write_selector_calibration_fixture(tmp_path: Path) -> dict[str, Path]:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-1"
    trace_id = "212a6a3417840231458777961e0d45"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "split": "validation",
                    "type": "HSF",
                    "data_ref": "snapshot",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": "Sentinel BlockException rate limit throttling",
                            "component": {"name": "sentinel-rule", "type": "limit"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "sentinel限流",
                                "description": "Sentinel BlockException rate limit throttling",
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {
                    "case_id": case_id,
                    "split": "validation",
                    "type": "HSF",
                    "data_ref": "snapshot",
                },
                "ontology": ["Case", "Service", "Trace", "MetricSeries", "LogError"],
                "retrieval_summary": "provider-app ProviderApi timeout",
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "provider-app:ProviderApi timeout",
                        "score": 9.0,
                        "reason": "ProviderApi HSFTimeOutException duration 10000ms",
                        "props": {
                            "trace_id": trace_id,
                            "client": "consumer-app",
                            "server": "provider-app",
                            "service": "com.alibaba.demo.ProviderApi@getThing",
                            "duration_ms": 10000,
                        },
                    }
                ],
                "evidence": [
                    {
                        "name": "trace_get",
                        "command": f"sf trace get {trace_id} -f json",
                        "returncode": 0,
                        "summary": "provider-app ProviderApi HSFTimeOutException timeout 10000ms",
                    },
                    {
                        "name": "metric_provider_rt",
                        "command": "sf metric query provider_rt -f json",
                        "returncode": 0,
                        "summary": "provider-app ProviderApi RT rose sharply",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    wrong_result = tmp_path / "wrong.json"
    wrong_result.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": (
                            "根因是 provider-app 的 ProviderApi 出现 HSFTimeOutException timeout，"
                            f"Trace {trace_id} 显示耗时 10000ms，provider RT 指标同步升高。"
                        ),
                        "trace_id": trace_id,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    right_result = tmp_path / "right.json"
    right_result.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": (
                            "根因是 Sentinel BlockException rate limit throttling，"
                            "触发限流后导致 HSF 请求被拒绝并引发成功率下降。"
                        ),
                        "trace_id": trace_id,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "dataset_dir": dataset_dir,
        "graph_root": graph_root,
        "wrong_result": wrong_result,
        "right_result": right_result,
    }
