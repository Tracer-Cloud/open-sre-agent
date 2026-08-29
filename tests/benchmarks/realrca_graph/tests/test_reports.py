from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.reports import build_triage_report, render_triage_markdown


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_triage_report_ranks_low_support_candidate_opportunity(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    graph_root = tmp_path / "graphs"
    dataset = tmp_path / "dataset"
    _write_json(
        dataset / "test.json",
        [
            {
                "case_id": case_id,
                "type": "HSF",
            }
        ],
    )
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "consumer-app success rate dropped.",
                    "trace_id": "trace-low",
                }
            ]
        },
    )
    _write_json(
        tmp_path / "candidate.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": (
                        "provider-app com.alibaba.demo.ProviderApi@getThing timed out. "
                        "Trace 212a6a3417840231458777961e0d45 shows provider-app duration 10000ms, "
                        "provider RT metric rose, and HSFTimeOutException appears in logs."
                    ),
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "HSF", "data_ref": "snapshot"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app:provider_group",
                    "score": 5.0,
                    "reason": "upstream provider timeout",
                    "props": {
                        "trace_id": "212a6a3417840231458777961e0d45",
                        "server": "provider-app:provider_group",
                        "service": "com.alibaba.demo.ProviderApi:1.0.0@getThing~P",
                    },
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                    "returncode": 0,
                    "summary": "provider-app ProviderApi getThing timeout at 10000ms",
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_rt",
                    "command": "sf metric query middleware_hsf_provider_service_method_rt -f json",
                    "returncode": 0,
                    "summary": "provider-app ProviderApi RT rose sharply in the alarm window",
                },
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app provider-app -f json",
                    "returncode": 0,
                    "summary": "HSFTimeOutException appears on provider-app",
                },
            ],
        },
    )

    report = build_triage_report(
        baseline_path=tmp_path / "baseline.json",
        graph_root=graph_root,
        split="test",
        candidate_paths=[tmp_path / "candidate.json"],
        dataset_dir=dataset,
    )

    top = report.cases[0]
    assert top.case_id == case_id
    assert top.best_candidate is not None
    assert top.best_candidate.support_delta > 0
    assert "人工读 evidence bundle" in top.action_hint


def test_render_triage_markdown_includes_table_and_case_notes(tmp_path) -> None:
    case_id = "case-a"
    graph_root = tmp_path / "graphs"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "database slow sql",
                    "trace_id": "trace",
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

    report = build_triage_report(
        baseline_path=tmp_path / "baseline.json",
        graph_root=graph_root,
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )
    markdown = render_triage_markdown(report)

    assert "| rank | case | type | priority |" in markdown
    assert "no_graph_hypothesis" in markdown
    assert "case-a" in markdown


def test_triage_report_uses_first_available_graph_root(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321bb"
    old_root = tmp_path / "old-graphs"
    new_root = tmp_path / "new-graphs"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "app-a cpu alarm",
                    "trace_id": "trace",
                }
            ]
        },
    )
    _write_json(
        old_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "CPU"},
            "root_candidates": [],
            "evidence": [],
        },
    )
    _write_json(
        new_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "CPU"},
            "root_candidates": [
                {
                    "kind": "metric",
                    "label": "jvm_gc_count:app=app-a",
                    "score": 5.0,
                    "reason": "gc count rose before cpu alarm",
                    "props": {"metric": "jvm_gc_count", "app_group": "app-a"},
                }
            ],
            "evidence": [
                {
                    "name": "metric_jvm_gc_count",
                    "command": "sf metric query jvm_gc_count -f json",
                    "returncode": 0,
                    "summary": "app-a jvm_gc_count rose before cpu alarm",
                }
            ],
        },
    )

    report = build_triage_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[new_root, old_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    assert report.graph_roots == [str(new_root), str(old_root)]
    assert report.cases[0].graph_path == str(new_root / "test" / case_id / "graph_context.json")
    assert report.cases[0].top_hypothesis == "jvm_gc_count:app=app-a"


def test_triage_report_marks_best_candidate_with_negative_probe_family(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321f4"
    graph_root = tmp_path / "graphs"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "consumer-app success rate dropped.",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        tmp_path / "results-test-evidence-gen-v3-weak-risky.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": (
                        "provider-app com.alibaba.demo.ProviderApi@getThing timed out. "
                        "Trace 212a6a3417840231458777961e0d45 shows provider duration 10000ms, "
                        "provider RT metric rose, and HSFTimeOutException appears in logs."
                    ),
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        tmp_path / "leaderboard.json",
        {
            "items": [
                {
                    "team_name": "隐元玩一玩",
                    "agent_name": "probe-gselect-21f8",
                    "accuracy": 84.85,
                },
                {
                    "team_name": "隐元玩一玩",
                    "agent_name": "probe-evidencegenv3-21f4",
                    "accuracy": 81.82,
                },
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
                    "reason": "provider-app ProviderApi timeout",
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "summary": "provider-app ProviderApi timeout",
                },
                {
                    "name": "metric_rt",
                    "summary": "provider-app ProviderApi RT rose",
                },
            ],
        },
    )

    report = build_triage_report(
        baseline_path=tmp_path / "baseline.json",
        graph_root=graph_root,
        split="test",
        candidate_paths=[tmp_path / "results-test-evidence-gen-v3-weak-risky.json"],
        dataset_dir=tmp_path / "missing-dataset",
        leaderboard_path=tmp_path / "leaderboard.json",
    )

    best_candidate = report.cases[0].best_candidate
    assert best_candidate is not None
    assert "negative_leaderboard_probe_family" in best_candidate.risks


def test_triage_report_ignores_candidate_identical_to_baseline(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    graph_root = tmp_path / "graphs"
    row = {
        "case_id": case_id,
        "diagnosis_output": "provider-app HSF timeout",
        "trace_id": "212a6a3417840231458777961e0d45",
    }
    _write_json(tmp_path / "baseline.json", {"results": [row]})
    _write_json(tmp_path / "candidate.json", {"results": [row]})
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app:provider_group",
                    "score": 5.0,
                    "reason": "provider-app timeout",
                }
            ],
            "evidence": [{"name": "trace_get", "summary": "provider-app timeout"}],
        },
    )

    report = build_triage_report(
        baseline_path=tmp_path / "baseline.json",
        graph_root=graph_root,
        split="test",
        candidate_paths=[tmp_path / "candidate.json"],
        dataset_dir=tmp_path / "missing-dataset",
    )

    assert report.cases[0].best_candidate is None
