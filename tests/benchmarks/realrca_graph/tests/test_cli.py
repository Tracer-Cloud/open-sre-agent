from __future__ import annotations

import argparse
import json

from tests.benchmarks.realrca_graph.cli import (
    _candidate_paths,
    _candidate_pool,
    _graph_roots,
    main,
)
from tests.benchmarks.realrca_graph.io import TEST_GRAPH_ROOT_PROFILE


def test_bundle_cli_writes_bundle(tmp_path) -> None:
    graph = tmp_path / "graph_context.json"
    graph.write_text(
        json.dumps(
            {
                "case": {"case_id": "case-1", "split": "test", "type": "SQL", "data_ref": "snap"},
                "ontology": ["Case", "SQL"],
                "retrieval_summary": "",
                "root_candidates": [
                    {
                        "kind": "sql",
                        "label": "rm-abc slow SQL",
                        "score": 4.5,
                        "reason": "slow SQL near alarm",
                        "props": {"instance_id": "rm-abc", "sql_id": "sql_123"},
                    }
                ],
                "evidence": [
                    {
                        "name": "diagnose_rds_sql",
                        "command": "sf diagnose rds-sql --instance-id rm-abc -f json",
                        "returncode": 0,
                        "summary": "rm-abc sql_id=sql_123 slow SQL latency rose",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "bundle.json"

    assert main(["bundle", "--graph", str(graph), "--out", str(out)]) == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["case_id"] == "case-1"
    assert payload["hypotheses"][0]["root_layer"] == "database"


def test_synthesize_cli_uses_first_available_graph_root(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-1"
    (dataset_dir / "test.json").write_text(
        json.dumps([{"case_id": case_id, "split": "test", "type": "HSF"}]),
        encoding="utf-8",
    )
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    graph_path = second_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "test", "type": "HSF", "data_ref": "snap"},
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "provider-app:provider_group",
                        "score": 5.0,
                        "reason": "provider timeout",
                        "props": {"trace_id": "212a6a3417840231458777961e0d45"},
                    }
                ],
                "evidence": [
                    {
                        "name": "trace_get",
                        "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                        "returncode": 0,
                        "summary": "provider-app ProviderApi timeout at 10000ms",
                    },
                    {
                        "name": "metric_rt",
                        "command": "sf metric query provider_rt -f json",
                        "returncode": 0,
                        "summary": "provider-app RT rose",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    result = tmp_path / "result.json"

    assert (
        main(
            [
                "synthesize",
                "--graph-root",
                str(first_root),
                "--graph-root",
                str(second_root),
                "--dataset-dir",
                str(dataset_dir),
                "--out-result",
                str(result),
            ]
        )
        == 0
    )

    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["results"][0]["case_id"] == case_id
    assert "provider-app:provider_group" in payload["results"][0]["diagnosis_output"]


def test_augment_graphs_cli_writes_augmented_graphs_and_skips_missing_runs(tmp_path) -> None:
    graph_root = tmp_path / "graphs"
    run_root = tmp_path / "runs"
    out_root = tmp_path / "augmented"
    case_id = "case-1"
    skipped_case_id = "case-2"
    for current_case_id in [case_id, skipped_case_id]:
        graph_path = graph_root / "test" / current_case_id / "graph_context.json"
        graph_path.parent.mkdir(parents=True)
        graph_path.write_text(
            json.dumps(
                {"case": {"case_id": current_case_id}, "evidence": [], "root_candidates": []},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    run_path = run_root / "test" / case_id / "run-final.json"
    run_path.parent.mkdir(parents=True)
    run_rows = [
        {
            "logItem": {
                "content": (
                    'BigBagWideDetailMgrProcessor search, request: {"pagination":{"pageNo":3,'
                    '"pageSize":500},"query":{"inboundBatchCode":{"$in":["A","B","C","D",'
                    '"E"]}},"userContext":{"requestUri":'
                    '"/api/method/main/bigBagWideDetailMgr/export"}}'
                )
            }
        }
    ]
    run_path.write_text(
        json.dumps(
            {
                "output": [
                    {
                        "event": "agent.tool_result",
                        "data": {
                            "result": [{"tool_use_id": "toolu_1", "content": json.dumps(run_rows)}]
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out_json = tmp_path / "augment-status.json"

    assert (
        main(
            [
                "augment-graphs",
                "--graph-root",
                str(graph_root),
                "--run-root",
                str(run_root),
                "--out-root",
                str(out_root),
                "--out-json",
                str(out_json),
            ]
        )
        == 0
    )

    augmented = json.loads(
        (out_root / "test" / case_id / "graph_context.json").read_text(encoding="utf-8")
    )
    assert augmented["root_candidates"][0]["kind"] == "heavy_business_query"
    status = json.loads(out_json.read_text(encoding="utf-8"))
    assert status["statuses"][0]["status"] == "wrote"
    assert status["statuses"][1] == {
        "case_id": skipped_case_id,
        "status": "skipped",
        "reason": "missing run-final",
    }


def test_augment_resolved_graphs_cli_uses_first_available_graph_root(tmp_path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    run_root = tmp_path / "runs"
    out_root = tmp_path / "out"
    dataset_dir = tmp_path / "dataset"
    case_id = "case-1"
    dataset_dir.mkdir()
    (dataset_dir / "test.json").write_text(json.dumps([{"case_id": case_id}]), encoding="utf-8")
    graph_path = second_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps({"case": {"case_id": case_id}, "evidence": [], "root_candidates": []}),
        encoding="utf-8",
    )
    run_path = run_root / "test" / case_id / "run-final.json"
    run_path.parent.mkdir(parents=True)
    run_rows = [
        {
            "logItem": {
                "content": (
                    'BigBagWideDetailMgrProcessor search, request: {"pagination":{"pageNo":3,'
                    '"pageSize":500},"query":{"inboundBatchCode":{"$in":["A","B","C","D",'
                    '"E","F","G","H","I"]}},"userContext":{"requestUri":'
                    '"/api/method/main/bigBagWideDetailMgr/export"}}'
                )
            }
        }
    ]
    run_path.write_text(
        json.dumps(
            {
                "output": [
                    {
                        "event": "agent.tool_result",
                        "data": {
                            "result": [
                                {
                                    "tool_use_id": "toolu_1",
                                    "content": json.dumps(run_rows, ensure_ascii=False),
                                }
                            ]
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    audit = tmp_path / "audit.json"

    assert (
        main(
            [
                "augment-resolved-graphs",
                "--graph-root",
                str(first_root),
                "--graph-root",
                str(second_root),
                "--run-root",
                str(run_root),
                "--out-root",
                str(out_root),
                "--dataset-dir",
                str(dataset_dir),
                "--out-json",
                str(audit),
                "--source",
                "dma",
            ]
        )
        == 0
    )

    augmented = json.loads(
        (out_root / "test" / case_id / "graph_context.json").read_text(encoding="utf-8")
    )
    assert augmented["root_candidates"][0]["kind"] == "heavy_business_query"
    payload = json.loads(audit.read_text(encoding="utf-8"))
    assert payload["statuses"][0]["source_graph"] == str(graph_path)
    assert payload["statuses"][0]["candidates_added"] == 1


def test_augment_resolved_graphs_cli_changed_only_skips_unchanged_graph(tmp_path) -> None:
    graph_root = tmp_path / "graphs"
    run_root = tmp_path / "runs"
    out_root = tmp_path / "out"
    dataset_dir = tmp_path / "dataset"
    case_id = "case-1"
    dataset_dir.mkdir()
    (dataset_dir / "test.json").write_text(json.dumps([{"case_id": case_id}]), encoding="utf-8")
    graph_path = graph_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps({"case": {"case_id": case_id}, "evidence": [], "root_candidates": []}),
        encoding="utf-8",
    )
    run_path = run_root / "test" / case_id / "run-final.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_text(
        json.dumps(
            {
                "output": [
                    {
                        "event": "agent.tool_result",
                        "data": {"result": [{"tool_use_id": "toolu_1", "content": "[]"}]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    audit = tmp_path / "audit.json"

    assert (
        main(
            [
                "augment-resolved-graphs",
                "--graph-root",
                str(graph_root),
                "--run-root",
                str(run_root),
                "--out-root",
                str(out_root),
                "--dataset-dir",
                str(dataset_dir),
                "--changed-only",
                "--out-json",
                str(audit),
            ]
        )
        == 0
    )

    assert not (out_root / "test" / case_id / "graph_context.json").exists()
    payload = json.loads(audit.read_text(encoding="utf-8"))
    assert payload["statuses"][0]["status"] == "unchanged"


def test_select_cli_can_skip_previously_probed_suffix(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321cc"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "根因：consumer-app 调用失败。",
                        "trace_id": "212a6a3417840231458777961e0d45",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": (
                            "根因：provider-app 的 com.alibaba.demo.ProviderApi@getThing "
                            "在 Trace 和指标中同时超时。"
                        ),
                        "trace_id": "212a6a3417840231458777961e0d45",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    leaderboard = tmp_path / "leaderboard.json"
    leaderboard.write_text(
        json.dumps(
            {"items": [{"team_name": "隐元玩一玩", "agent_name": "probe-any-21cc"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
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
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = tmp_path / "result.json"
    audit = tmp_path / "audit.json"

    assert (
        main(
            [
                "select",
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--leaderboard",
                str(leaderboard),
                "--skip-probed-cases",
                "--graph-root",
                str(graph_root),
                "--out-result",
                str(result),
                "--out-audit",
                str(audit),
                "--min-support",
                "0.1",
                "--min-margin",
                "0.0",
                "--min-modalities",
                "1",
            ]
        )
        == 0
    )

    row = json.loads(result.read_text(encoding="utf-8"))["results"][0]
    assert row["diagnosis_output"] == "根因：consumer-app 调用失败。"
    audit_payload = json.loads(audit.read_text(encoding="utf-8"))
    assert audit_payload["accepted_replacements"] == []
    assert audit_payload["skipped_probed_case_ids"] == [case_id]


def test_candidate_pool_deduplicates_identical_rows(tmp_path) -> None:
    case_id = "case-1"
    first = tmp_path / "results-test-a.json"
    second = tmp_path / "results-test-b.json"
    payload = {
        "results": [
            {
                "case_id": case_id,
                "diagnosis_output": "same answer",
                "trace_id": "21086dd417794301073866378e7584",
            }
        ]
    }
    first.write_text(json.dumps(payload), encoding="utf-8")
    second.write_text(json.dumps(payload), encoding="utf-8")

    pool = _candidate_pool([first, second])

    assert len(pool[case_id]) == 1


def test_candidate_paths_expands_globs_and_deduplicates_existing_files(tmp_path) -> None:
    pool_dir = tmp_path / "pool"
    nested_dir = pool_dir / "nested"
    nested_dir.mkdir(parents=True)
    first = pool_dir / "results-test-a.json"
    second = pool_dir / "results-test-b.json"
    nested = nested_dir / "replacements-test-c.json"
    for path in [first, second, nested]:
        path.write_text("{}", encoding="utf-8")

    paths = _candidate_paths(
        argparse.Namespace(
            candidate=[first, tmp_path / "missing.json"],
            candidate_glob=[str(pool_dir / "**" / "*.json")],
        )
    )

    assert paths[0] == first
    assert set(paths[1:]) == {second, nested}


def test_graph_root_profile_appends_after_explicit_roots(tmp_path) -> None:
    explicit = tmp_path / "graph-new"

    roots = _graph_roots(argparse.Namespace(graph_root=[explicit], graph_profile=["latest-test"]))

    assert roots[0] == explicit
    assert roots[1].name == TEST_GRAPH_ROOT_PROFILE[0]
    assert roots[-1].name == "graph-v1"


def test_tomography_cli_writes_report(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "reference answer",
                        "trace_id": "trace",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    leaderboard = tmp_path / "leaderboard.json"
    leaderboard.write_text(
        json.dumps(
            {
                "items": [
                    {"team_name": "隐元玩一玩", "agent_name": "ref", "accuracy": 10.0},
                    {"team_name": "隐元玩一玩", "agent_name": "probe-a", "accuracy": 9.0},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "submission-test-a.json").write_text(
        json.dumps(
            {
                "submission_response": {
                    "submission": {
                        "agent_name": "probe-a",
                        "team_name": "隐元玩一玩",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "results-test-a.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "candidate answer",
                        "trace_id": "trace",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out_json = tmp_path / "tomography.json"
    out_md = tmp_path / "tomography.md"

    assert (
        main(
            [
                "tomography",
                "--leaderboard",
                str(leaderboard),
                "--reference",
                str(reference),
                "--reference-agent-name",
                "ref",
                "--results-dir",
                str(tmp_path),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["matched_submission_count"] == 1
    assert payload["cases"][0]["best_estimate"] == -1.0
    assert "RealRCA Score Tomography" in out_md.read_text(encoding="utf-8")


def test_repair_traces_cli_preserves_diagnosis_and_writes_audit(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "provider-app HSF timeout.",
                        "trace_id": "codex-synthetic-id",
                    },
                    {
                        "case_id": "unchanged-case",
                        "diagnosis_output": "existing trace is already real.",
                        "trace_id": "2131988117846860280378393d11e1",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(
            {
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "provider-app:provider_group",
                        "score": 4.0,
                        "props": {
                            "trace_id": "212a6a3417840231458777961e0d45",
                            "server": "provider-app:provider_group",
                        },
                    }
                ],
                "evidence": [],
            }
        ),
        encoding="utf-8",
    )
    result = tmp_path / "result.json"
    audit = tmp_path / "audit.json"

    assert (
        main(
            [
                "repair-traces",
                "--baseline",
                str(baseline),
                "--graph-root",
                str(graph_root),
                "--case-id",
                "321aa",
                "--allow-inferred-trace",
                "--out-result",
                str(result),
                "--out-audit",
                str(audit),
            ]
        )
        == 0
    )

    result_rows = json.loads(result.read_text(encoding="utf-8"))["results"]
    repaired = {row["case_id"]: row for row in result_rows}
    assert repaired[case_id]["trace_id"] == "212a6a3417840231458777961e0d45"
    assert repaired[case_id]["diagnosis_output"] == "provider-app HSF timeout."
    assert repaired["unchanged-case"]["trace_id"] == "2131988117846860280378393d11e1"

    audit_payload = json.loads(audit.read_text(encoding="utf-8"))
    assert audit_payload["repaired_case_ids"] == [case_id]


def test_enrich_trajectories_cli_writes_full_result_and_audit(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321bb"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": (
                            "根因：provider-app 的 com.alibaba.demo.ProviderApi@getThing "
                            "调用失败导致成功率下降。"
                        ),
                        "trace_id": "212a6a3417840231458777961e0d45",
                    },
                    {
                        "case_id": "unchanged-case",
                        "diagnosis_output": "根因：已有答案保持。",
                        "trace_id": "2131988117846860280378393d11e1",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    trajectory_audit = tmp_path / "trajectory-audit.json"
    trajectory_audit.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "missing_terms": [
                            {
                                "term": "HSF-0001",
                                "kind": "hsf_code",
                                "score": 24,
                                "count": 3,
                                "graph_supported": True,
                                "event_counts": {"agent.tool_result": 3},
                                "occurrences": [
                                    {"snippet": "provider-app ProviderApi returned HSF-0001"}
                                ],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "test", "type": "HSF"},
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "provider-app:provider_group",
                        "score": 4.0,
                        "reason": "provider-app ProviderApi HSF-0001",
                    }
                ],
                "evidence": [
                    {
                        "name": "trace_get",
                        "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                        "returncode": 0,
                        "summary": "provider-app ProviderApi returned HSF-0001",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = tmp_path / "result.json"
    audit = tmp_path / "audit.json"

    assert (
        main(
            [
                "enrich-trajectories",
                "--baseline",
                str(baseline),
                "--audit",
                str(trajectory_audit),
                "--graph-root",
                str(graph_root),
                "--out-result",
                str(result),
                "--out-audit",
                str(audit),
            ]
        )
        == 0
    )

    result_rows = json.loads(result.read_text(encoding="utf-8"))["results"]
    rows = {row["case_id"]: row for row in result_rows}
    assert "HSF-0001" in rows[case_id]["diagnosis_output"]
    assert rows[case_id]["trace_id"] == "212a6a3417840231458777961e0d45"
    assert rows["unchanged-case"]["diagnosis_output"] == "根因：已有答案保持。"

    audit_payload = json.loads(audit.read_text(encoding="utf-8"))
    assert audit_payload["changed_case_ids"] == [case_id]


def test_enrich_trajectories_cli_can_skip_previously_probed_suffix(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321bb"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": (
                            "根因：provider-app 的 com.alibaba.demo.ProviderApi@getThing "
                            "调用失败导致成功率下降。"
                        ),
                        "trace_id": "212a6a3417840231458777961e0d45",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    trajectory_audit = tmp_path / "trajectory-audit.json"
    trajectory_audit.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "missing_terms": [
                            {
                                "term": "HSF-0001",
                                "kind": "hsf_code",
                                "score": 24,
                                "graph_supported": True,
                                "event_counts": {"agent.tool_result": 3},
                                "occurrences": [
                                    {"snippet": "provider-app ProviderApi returned HSF-0001"}
                                ],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    leaderboard = tmp_path / "leaderboard.json"
    leaderboard.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "team_name": "隐元玩一玩",
                        "agent_name": "probe-any-21bb",
                        "accuracy": 82.83,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "test", "type": "HSF"},
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "provider-app:provider_group",
                        "score": 4.0,
                        "reason": "provider-app ProviderApi HSF-0001",
                    }
                ],
                "evidence": [
                    {
                        "name": "trace_get",
                        "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                        "returncode": 0,
                        "summary": "provider-app ProviderApi returned HSF-0001",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = tmp_path / "result.json"
    audit = tmp_path / "audit.json"

    assert (
        main(
            [
                "enrich-trajectories",
                "--baseline",
                str(baseline),
                "--audit",
                str(trajectory_audit),
                "--leaderboard",
                str(leaderboard),
                "--skip-probed-cases",
                "--graph-root",
                str(graph_root),
                "--out-result",
                str(result),
                "--out-audit",
                str(audit),
            ]
        )
        == 0
    )

    row = json.loads(result.read_text(encoding="utf-8"))["results"][0]
    assert "HSF-0001" not in row["diagnosis_output"]
    audit_payload = json.loads(audit.read_text(encoding="utf-8"))
    assert audit_payload["changed_case_ids"] == []
    assert audit_payload["decisions"][0]["reason"].startswith("skipped:")


def test_generate_candidates_cli_dry_run_writes_prompt_package_and_empty_partial_result(
    tmp_path,
) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321cc"
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "test.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "split": "test",
                    "type": "HSF",
                    "name": "hidden title",
                    "reference": "hidden answer",
                    "root_cause_chain": ["hidden"],
                    "meta": {"alarm_id": "alarm-1", "name": "hidden meta"},
                    "data_ref": "snapshot-1",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    result_row = {
        "case_id": case_id,
        "diagnosis_output": "根因：provider-app HSF 调用超时。",
        "trace_id": "212a6a3417840231458777961e0d45",
    }
    baseline.write_text(
        json.dumps(
            {"results": [result_row]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    identical_candidate = tmp_path / "candidate.json"
    identical_candidate.write_text(
        json.dumps({"results": [result_row]}, ensure_ascii=False),
        encoding="utf-8",
    )
    trajectory_audit = tmp_path / "trajectory-audit.json"
    trajectory_audit.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "missing_terms": [
                            {
                                "term": "HSF-0002",
                                "kind": "hsf_code",
                                "score": 21,
                                "graph_supported": True,
                                "source_runs": ["internal-run"],
                                "event_counts": {"agent.tool_result": 2},
                                "occurrences": [{"snippet": "provider-app returned HSF-0002"}],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    frontier = tmp_path / "frontier.json"
    frontier.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": case_id,
                        "case_suffix": "21cc",
                        "bucket": "raw_mechanism_probe",
                        "frontier_score": 3.2,
                        "raw_uncovered_mechanisms": ["hsf_threadpool_busy"],
                        "signals": ["raw_boundary_mechanism_gap"],
                        "blockers": ["case_negative_probe_history"],
                        "top_hypothesis": "provider-app THREADPOOL_BUSY",
                        "graph_path": "/tmp/local/graph_context.json",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "test", "type": "HSF"},
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "provider-app:provider_group",
                        "score": 5.0,
                        "reason": "provider-app HSF timeout",
                    }
                ],
                "evidence": [
                    {
                        "name": "trace_get",
                        "summary": "provider-app returned HSF-0001",
                        "raw_path": "/tmp/local/raw.json",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = tmp_path / "partial-result.json"
    audit = tmp_path / "audit.json"
    out_dir = tmp_path / "runs"

    assert (
        main(
            [
                "generate-candidates",
                "--dry-run",
                "--baseline",
                str(baseline),
                "--candidate",
                str(identical_candidate),
                "--dataset-dir",
                str(dataset_dir),
                "--graph-root",
                str(graph_root),
                "--case-id",
                "21cc",
                "--out-result",
                str(result),
                "--out-audit",
                str(audit),
                "--out-dir",
                str(out_dir),
                "--run-label",
                "dry",
                "--validation-exemplar-limit",
                "0",
                "--strategy-hint",
                "优先测试深层下游慢调用。",
                "--trajectory-audit",
                str(trajectory_audit),
                "--trajectory-term-limit",
                "3",
                "--frontier",
                str(frontier),
            ]
        )
        == 0
    )

    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["results"] == []
    audit_payload = json.loads(audit.read_text(encoding="utf-8"))
    assert audit_payload["generated_case_count"] == 0
    assert audit_payload["statuses"][0]["status"] == "packaged"
    prompt = (out_dir / "dry" / "test" / case_id / "prompt.txt").read_text(encoding="utf-8")
    package = json.loads(
        (out_dir / "dry" / "test" / case_id / "package.json").read_text(encoding="utf-8")
    )
    assert "hidden answer" not in prompt
    assert "优先测试深层下游慢调用" in prompt
    assert "HSF-0002" in prompt
    assert "internal-run" not in prompt
    assert "/tmp/local" not in prompt
    assert "frontier_differential" in prompt
    assert "name" not in package["case"]["meta"]
    assert package["frontier_context"]["raw_uncovered_mechanisms"] == ["hsf_threadpool_busy"]
    assert [item["source"] for item in package["candidate_summaries"]] == ["baseline"]
    assert package["visible_tool_signals"][0]["term"] == "HSF-0002"
    assert package["visible_tool_signals"][0]["tool_result_count"] == 2
    assert audit_payload["strategy_hint"] == "优先测试深层下游慢调用。"
    assert audit_payload["frontier"] == str(frontier)
    assert audit_payload["trajectory_audits"] == [str(trajectory_audit)]


def test_verify_candidates_cli_dry_run_writes_prompt_package_and_full_result(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321cc"
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "test.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "split": "test",
                    "type": "HSF",
                    "reference": "hidden answer",
                    "root_cause_chain": ["hidden"],
                    "meta": {"alarm_id": "alarm-1", "name": "hidden meta"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "根因：consumer-app 调用失败。",
                        "trace_id": "212a6a3417840231458777961e0d45",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "根因：provider-app 的 ProviderApi 方法超时。",
                        "trace_id": "212a6a3417840231458777961e0d45",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(
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
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = tmp_path / "verified-result.json"
    audit = tmp_path / "verify-audit.json"
    out_dir = tmp_path / "runs"

    assert (
        main(
            [
                "verify-candidates",
                "--dry-run",
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--dataset-dir",
                str(dataset_dir),
                "--graph-root",
                str(graph_root),
                "--case-id",
                "21cc",
                "--out-result",
                str(result),
                "--out-audit",
                str(audit),
                "--out-dir",
                str(out_dir),
                "--run-label",
                "verify-dry",
            ]
        )
        == 0
    )

    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["results"][0]["diagnosis_output"] == "根因：consumer-app 调用失败。"
    audit_payload = json.loads(audit.read_text(encoding="utf-8"))
    assert audit_payload["verified_pair_count"] == 0
    assert audit_payload["statuses"][0]["status"] == "packaged"
    prompt = (out_dir / "verify-dry" / "test" / case_id / "candidate" / "prompt.txt").read_text(
        encoding="utf-8"
    )
    package = json.loads(
        (out_dir / "verify-dry" / "test" / case_id / "candidate" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    assert "hidden answer" not in prompt
    assert package["challenger_answer"]["diagnosis_output"].startswith("根因：provider-app")
    assert "name" not in package["case"]["meta"]
