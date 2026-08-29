from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.case_analogues import (
    build_case_analogue_report,
    profile_from_bundle,
)
from tests.benchmarks.realrca_graph.cli import main


def test_profile_analogue_matching_prefers_causal_mechanism_over_app_overlap(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "根因：checkout-app 调用 provider Sentinel 限流导致失败。",
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
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "test", "type": "HSF"},
                "root_candidates": [
                    {
                        "kind": "pattern_limit",
                        "label": "checkout-app provider sentinel limit",
                        "score": 7.0,
                        "reason": "HSF provider SentinelBlockException interface limit",
                    }
                ],
                "evidence": [
                    {
                        "name": "metric_hsf_error_qps",
                        "summary": "checkout-app provider SentinelBlockException error qps rising",
                    },
                    {
                        "name": "trace_get",
                        "summary": "checkout-app provider SentinelBlockException in trace",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memory = tmp_path / "memory.json"
    memory.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "case_id": "validation-limit",
                        "case_type": "HSF",
                        "feature_tokens": ["app:unrelated-app", "kind:pattern_limit"],
                        "truth": {
                            "root_cause_chain": [
                                {
                                    "type": "root_cause",
                                    "description": "接口 QPS 突增触发 Sentinel 限流",
                                    "component": {"name": "provider", "type": "app"},
                                }
                            ]
                        },
                        "graph": {"retrieval_summary": "Sentinel block and HSF error qps"},
                    },
                    {
                        "case_id": "validation-sql-same-app",
                        "case_type": "TDDL",
                        "feature_tokens": ["app:checkout-app", "kind:pattern_slow_sql"],
                        "truth": {
                            "root_cause_chain": [
                                {
                                    "type": "root_cause",
                                    "description": "慢 SQL 导致数据库超时",
                                    "component": {"name": "orders", "type": "db"},
                                }
                            ]
                        },
                        "graph": {"retrieval_summary": "slow sql"},
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_case_analogue_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        validation_memory_path=memory,
    )

    assert report.cases[0].matches[0].case_id == "validation-limit"
    assert "limit" in report.cases[0].matches[0].matched_mechanisms


def test_case_analogue_report_flags_baseline_layer_diff(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321bb"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "根因：provider Sentinel 限流导致调用失败。",
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
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "test", "type": "TDDL"},
                "root_candidates": [
                    {
                        "kind": "pattern_slow_sql",
                        "label": "orders slow SQL",
                        "score": 8.0,
                        "reason": "TDDL_QUERY orders duration=2500ms 慢SQL",
                    }
                ],
                "evidence": [
                    {"name": "trace_get", "summary": "TDDL_QUERY@mall:orders duration=2500"},
                    {"name": "diagnose_rds_sql", "summary": "orders slow SQL full scan"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memory = tmp_path / "memory.json"
    memory.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "case_id": "validation-sql",
                        "case_type": "TDDL",
                        "feature_tokens": ["kind:pattern_slow_sql", "sql_table:orders"],
                        "truth": {
                            "root_cause_chain": [
                                {
                                    "type": "root_cause",
                                    "description": "orders 慢 SQL 全表扫描",
                                    "component": {"name": "orders", "type": "db"},
                                }
                            ]
                        },
                        "graph": {"retrieval_summary": "TDDL slow sql"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_case_analogue_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        validation_memory_path=memory,
    )

    assert any(
        category.startswith("baseline_layer_diff:") for category in report.cases[0].categories
    )


def test_case_analogue_report_marks_known_negative_probe(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "根因：orders 慢 SQL 导致超时。",
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
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "test", "type": "TDDL"},
                "root_candidates": [
                    {
                        "kind": "pattern_slow_sql",
                        "label": "orders slow SQL",
                        "score": 8.0,
                        "reason": "orders 慢SQL timeout",
                    }
                ],
                "evidence": [
                    {"name": "trace_get", "summary": "TDDL_QUERY@mall:orders timeout"},
                    {"name": "diagnose_rds_sql", "summary": "orders slow SQL"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memory = tmp_path / "memory.json"
    memory.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "case_id": "validation-sql",
                        "case_type": "TDDL",
                        "feature_tokens": ["kind:pattern_slow_sql"],
                        "truth": {
                            "root_cause_chain": [
                                {
                                    "type": "root_cause",
                                    "description": "orders 慢 SQL",
                                    "component": {"name": "orders", "type": "db"},
                                }
                            ]
                        },
                        "graph": {"retrieval_summary": "slow sql"},
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
                    {"team_name": "隐元玩一玩", "agent_name": "probe-best-21f8", "accuracy": 84.85},
                    {"team_name": "隐元玩一玩", "agent_name": "probe-old-21aa", "accuracy": 82.83},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_case_analogue_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        validation_memory_path=memory,
        leaderboard_path=leaderboard,
    )

    assert "known_negative_probe" in report.cases[0].categories
    assert report.cases[0].probe_count == 1
    assert report.cases[0].best_probe_accuracy == 82.83


def test_case_analogues_cli_writes_report(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321cc"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "根因：Redis/Tair 缓存访问 timeout。",
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
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "test", "type": "Tair"},
                "root_candidates": [
                    {
                        "kind": "pattern_cache_timeout",
                        "label": "redis r-abc timeout",
                        "score": 6.0,
                        "reason": "JedisConnectionException timeout",
                    }
                ],
                "evidence": [
                    {"name": "trace_get", "summary": "redis r-abc timeout"},
                    {"name": "metric_tair_rt", "summary": "redis r-abc rt rising"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memory = tmp_path / "memory.json"
    memory.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "case_id": "validation-cache",
                        "case_type": "Tair",
                        "feature_tokens": ["kind:pattern_cache_timeout", "app:some-app"],
                        "truth": {
                            "root_cause_chain": [
                                {
                                    "type": "root_cause",
                                    "description": "Redis 缓存 timeout",
                                    "component": {"name": "r-abc", "type": "tair"},
                                }
                            ]
                        },
                        "graph": {"retrieval_summary": "cache timeout"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out_json = tmp_path / "analogues.json"
    out_md = tmp_path / "analogues.md"

    assert (
        main(
            [
                "case-analogues",
                "--baseline",
                str(baseline),
                "--graph-root",
                str(graph_root),
                "--validation-memory",
                str(memory),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["cases"][0]["matches"][0]["case_id"] == "validation-cache"
    assert "RealRCA Case Analogues" in out_md.read_text(encoding="utf-8")


def test_profile_from_bundle_extracts_mechanisms_and_modalities() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "METAQ"},
            "root_candidates": [
                {
                    "kind": "pattern_notify_business_failure",
                    "label": "notify receive BIZ_ERROR",
                    "score": 5.0,
                    "reason": "ConsumeMessageThread msgId BIZ_ERROR",
                }
            ],
            "evidence": [
                {"name": "sls_app_logs", "summary": "ConsumeMessageThread BIZ_ERROR"},
                {"name": "metric_mq", "summary": "MetaQ consumer success rate drops"},
            ],
        }
    )

    profile = profile_from_bundle(bundle)

    assert {"consume_failure", "mq"} <= set(profile.mechanisms)
    assert {"log", "metric"} <= set(profile.modalities)


def test_profile_from_bundle_extracts_custom_monitor_business_metric() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "自定义监控"},
            "root_candidates": [
                {
                    "kind": "custom_monitor_signal",
                    "label": "1026_SPM_19:失败数:代理名=gocBlockout",
                    "score": 4.6,
                    "reason": "custom monitor metric max=66 trend=rising",
                }
            ],
            "evidence": [
                {
                    "name": "metric_custom_1026_spm_19_失败数",
                    "summary": "[代理名=gocBlockout] max=66 trend=rising",
                }
            ],
        }
    )

    profile = profile_from_bundle(bundle)

    assert "business_metric" in profile.mechanisms
    assert profile.root_layers == ["application"]
    assert "metric" in profile.modalities
