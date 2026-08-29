from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.frontier import (
    build_frontier_report,
    render_frontier_markdown,
)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _case_id(suffix: str) -> str:
    return f"01a0330f-29a8-7e83-8121-3bf4cce3{suffix}"


def _baseline_row(
    case_id: str, diagnosis: str, trace_id: str = "212a6a3417840231458777961e0d45"
) -> dict[str, str]:
    return {"case_id": case_id, "diagnosis_output": diagnosis, "trace_id": trace_id}


def _memory(path) -> None:
    _write_json(path, {"entries": []})


def test_frontier_blocks_known_negative_without_new_root_mechanism(tmp_path) -> None:
    stable_case = _case_id("21aa")
    raw_gap_case = _case_id("21bb")
    graph_root = tmp_path / "graphs"
    baseline = tmp_path / "baseline.json"
    memory = tmp_path / "memory.json"
    leaderboard = tmp_path / "leaderboard.json"
    _memory(memory)
    _write_json(
        baseline,
        {
            "results": [
                _baseline_row(
                    stable_case, "根因：checkout-provider 的 Sentinel 限流导致 HSF 快速失败。"
                ),
                _baseline_row(
                    raw_gap_case, "根因：pay-provider 的 Sentinel 限流导致 HSF 快速失败。"
                ),
            ]
        },
    )
    _write_json(
        leaderboard,
        {
            "items": [
                {
                    "team_name": "隐元玩一玩",
                    "agent_name": "probe-best-reference",
                    "accuracy": 84.85,
                },
                {"team_name": "隐元玩一玩", "agent_name": "probe-old-21aa", "accuracy": 82.83},
                {"team_name": "隐元玩一玩", "agent_name": "probe-old-21bb", "accuracy": 82.83},
            ]
        },
    )
    for case_id, app in ((stable_case, "checkout-provider"), (raw_gap_case, "pay-provider")):
        case_dir = graph_root / "test" / case_id
        _write_json(
            case_dir / "graph_context.json",
            {
                "case": {"case_id": case_id, "split": "test", "type": "HSF"},
                "root_candidates": [
                    {
                        "kind": "pattern_limit",
                        "label": f"{app} SentinelBlockException",
                        "score": 7.0,
                        "reason": "SentinelBlockException 快速失败 HSF provider success_rate dropped",
                    }
                ],
                "evidence": [
                    {
                        "name": "metric_hsf_provider_success_rate",
                        "summary": f"{app} provider success_rate dropped, RT decreased",
                    },
                    {
                        "name": "trace_get",
                        "summary": f"{app} trace SentinelBlockException",
                    },
                ],
            },
        )
    _write_json(
        graph_root / "test" / raw_gap_case / "raw" / "sls_app_threadpool.json",
        [{"message": ("HSF-0002 THREADPOOL_BUSY HSFTimeOutException provider_ip=33.62.98.154")}],
    )

    report = build_frontier_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
        validation_memory_path=memory,
        leaderboard_path=leaderboard,
    )
    by_suffix = {item.case_suffix: item for item in report.cases}

    assert by_suffix["21aa"].bucket == "do_not_probe"
    assert "known_negative_probe" in by_suffix["21aa"].blockers
    assert by_suffix["21bb"].bucket == "raw_mechanism_probe"
    assert "raw_boundary_mechanism_gap" in by_suffix["21bb"].signals


def test_frontier_distinguishes_same_family_and_untried_negative_probe(tmp_path) -> None:
    same_family_case = _case_id("21ab")
    untried_family_case = _case_id("21ac")
    graph_root = tmp_path / "graphs"
    baseline = tmp_path / "baseline.json"
    memory = tmp_path / "memory.json"
    leaderboard = tmp_path / "leaderboard.json"
    _memory(memory)
    _write_json(
        baseline,
        {
            "results": [
                _baseline_row(same_family_case, "根因：checkout-provider HSF 快速失败。"),
                _baseline_row(untried_family_case, "根因：seller-provider HSF 快速失败。"),
            ]
        },
    )
    _write_json(
        leaderboard,
        {
            "items": [
                {
                    "team_name": "隐元玩一玩",
                    "agent_name": "probe-best-reference",
                    "accuracy": 84.85,
                },
                {
                    "team_name": "隐元玩一玩",
                    "agent_name": "probe-sentinel-change-21ab",
                    "accuracy": 82.83,
                },
                {
                    "team_name": "隐元玩一玩",
                    "agent_name": "probe-sentinel-change-21ac",
                    "accuracy": 82.83,
                },
            ]
        },
    )
    for case_id, app in (
        (same_family_case, "checkout-provider"),
        (untried_family_case, "seller-provider"),
    ):
        _write_json(
            graph_root / "test" / case_id / "graph_context.json",
            {
                "case": {"case_id": case_id, "split": "test", "type": "HSF"},
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": f"{app} timeout",
                        "score": 5.0,
                        "reason": "HSF timeout",
                    }
                ],
                "evidence": [{"name": "trace_get", "summary": f"{app} HSF timeout"}],
            },
        )
    _write_json(
        graph_root / "test" / same_family_case / "raw" / "sls_sentinel.json",
        [{"message": "SentinelBlockException flow control block request"}],
    )
    _write_json(
        graph_root / "test" / untried_family_case / "raw" / "rds_sql_stats.json",
        [{"message": "slow SQL TDDL_QUERY select * from trade_order duration=3000ms"}],
    )

    report = build_frontier_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
        validation_memory_path=memory,
        leaderboard_path=leaderboard,
    )
    by_suffix = {item.case_suffix: item for item in report.cases}

    assert "known_negative_probe" in by_suffix["21ab"].blockers
    assert "case_negative_probe_history" in by_suffix["21ac"].blockers
    assert "untried_root_mechanism_after_negative" in by_suffix["21ac"].signals
    assert "known_negative_probe" not in by_suffix["21ac"].blockers


def test_frontier_ignores_raw_mechanism_excluded_by_baseline(tmp_path) -> None:
    case_id = _case_id("21ad")
    graph_root = tmp_path / "graphs"
    baseline = tmp_path / "baseline.json"
    memory = tmp_path / "memory.json"
    _memory(memory)
    _write_json(
        baseline,
        {
            "results": [
                _baseline_row(
                    case_id,
                    "根因：provider-app Sentinel 限流导致 HSF 快速失败。排除 TDDL/RDS 主因：无 SQL 慢查询或锁等待证据。",
                ),
            ]
        },
    )
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "pattern_limit",
                    "label": "provider-app SentinelBlockException",
                    "score": 7.0,
                    "reason": "SentinelBlockException 快速失败 HSF provider success_rate dropped",
                }
            ],
            "evidence": [{"name": "trace_get", "summary": "SentinelBlockException rc=1"}],
        },
    )
    _write_json(
        graph_root / "test" / case_id / "raw" / "rds_sql_stats.json",
        [{"message": "slow SQL TDDL_QUERY select * from side_table duration=3000ms"}],
    )

    report = build_frontier_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
        validation_memory_path=memory,
    )
    case = report.cases[0]

    assert "raw_mechanism_excluded_by_baseline" in case.signals
    assert "raw_boundary_mechanism_gap" not in case.signals


def test_frontier_demotes_trace_sql_sidecar_when_cache_root_is_supported(tmp_path) -> None:
    case_id = _case_id("1d71")
    graph_root = tmp_path / "graphs"
    baseline = tmp_path / "baseline.json"
    memory = tmp_path / "memory.json"
    _memory(memory)
    _write_json(
        baseline,
        {
            "results": [
                _baseline_row(
                    case_id,
                    "根因：RedisCacheManager 调用 Jedis.mget 发生 Socket Read timed out。",
                ),
            ]
        },
    )
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "Tair"},
            "root_candidates": [
                {
                    "kind": "pattern_cache_timeout",
                    "label": "cache_timeout",
                    "score": 7.0,
                    "reason": "JedisConnectionException Read timed out",
                }
            ],
            "evidence": [
                {"name": "log_error_list", "summary": "JedisConnectionException Read timed out"}
            ],
        },
    )
    _write_json(
        graph_root / "test" / case_id / "raw" / "trace_get_with_side_sql.json",
        [
            {
                "summary": (
                    "same sampled trace has a side branch SQL span "
                    "TDDL_QUERY@global_uic_ae_0007:global_user\\u001ae6c547cf "
                    "duration_ms=3000 and unique_key wording"
                )
            }
        ],
    )

    report = build_frontier_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
        validation_memory_path=memory,
    )
    case = report.cases[0]

    assert "raw_boundary_mechanism_gap" not in case.signals
    assert "sidecar_raw_mechanism:slow_sql" in case.raw_categories
    assert case.raw_uncovered_mechanisms == []
    assert case.bucket != "raw_mechanism_probe"


def test_frontier_blocks_top_hypothesis_negated_by_baseline(tmp_path) -> None:
    case_id = _case_id("21ae")
    graph_root = tmp_path / "graphs"
    baseline = tmp_path / "baseline.json"
    memory = tmp_path / "memory.json"
    _memory(memory)
    _write_json(
        baseline,
        {
            "results": [
                _baseline_row(
                    case_id,
                    (
                        "根因定位：provider-app 单机 Full GC 导致 JVM 长时间停顿，"
                        "不是 HSF 线程池打满；线程池使用率只有 18%，排除线程池容量问题。"
                    ),
                ),
            ]
        },
    )
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "pattern_hsf_threadpool_timeout",
                    "label": "provider-app THREADPOOL_BUSY threadpool_busy@33.1.2.3",
                    "score": 9.0,
                    "reason": "HSF provider thread pool is full",
                }
            ],
            "evidence": [
                {"name": "trace_get", "summary": "provider-app HSF TIMEOUT"},
                {"name": "metric_hsf_rt", "summary": "provider RT rising"},
            ],
        },
    )

    report = build_frontier_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
        validation_memory_path=memory,
    )
    case = report.cases[0]

    assert "top_hypothesis_excluded_by_baseline" in case.signals
    assert "top_hypothesis_negated_by_baseline" in case.blockers
    assert case.bucket == "do_not_probe"


def test_frontier_only_boosts_synthetic_trace_when_raw_trace_changes_mechanism(tmp_path) -> None:
    trace_only_case = _case_id("21cc")
    direct_trace_case = _case_id("21dd")
    graph_root = tmp_path / "graphs"
    baseline = tmp_path / "baseline.json"
    memory = tmp_path / "memory.json"
    _memory(memory)
    _write_json(
        baseline,
        {
            "results": [
                _baseline_row(
                    trace_only_case,
                    "根因：cache-client 调用 Tair 超时。",
                    trace_id="codex-not-real",
                ),
                _baseline_row(
                    direct_trace_case,
                    "根因：cache-client 调用 Tair 超时。",
                    trace_id="codex-not-real",
                ),
            ]
        },
    )
    for case_id in (trace_only_case, direct_trace_case):
        _write_json(
            graph_root / "test" / case_id / "graph_context.json",
            {
                "case": {"case_id": case_id, "split": "test", "type": "Tair"},
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "cache-client tair call",
                        "score": 4.0,
                        "reason": "cache client call failed",
                    }
                ],
                "evidence": [{"name": "metric_tair_rt", "summary": "Tair RT changed"}],
            },
        )
    _write_json(
        graph_root / "test" / direct_trace_case / "raw" / "trace_list_tair.json",
        [
            {
                "traceId": "212a6a3417840231458777961e0d45",
                "message": "RedisCommandTimeoutException tair GET timeout after 50ms",
            }
        ],
    )

    report = build_frontier_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
        validation_memory_path=memory,
    )
    by_suffix = {item.case_suffix: item for item in report.cases}

    assert by_suffix["21cc"].bucket == "do_not_trace_repair_only"
    assert "trace_only_repair_without_direct_root_gap" in by_suffix["21cc"].blockers
    assert "direct_trace_mechanism_gap" in by_suffix["21dd"].signals
    assert by_suffix["21dd"].frontier_score > by_suffix["21cc"].frontier_score


def test_frontier_routes_weak_hsf_case_to_counterfactual_review(tmp_path) -> None:
    case_id = _case_id("21ee")
    graph_root = tmp_path / "graphs"
    baseline = tmp_path / "baseline.json"
    memory = tmp_path / "memory.json"
    _memory(memory)
    _write_json(
        baseline,
        {
            "results": [
                _baseline_row(case_id, "根因：订单 SQL 慢查询导致 HSF 成功率下降。"),
            ]
        },
    )
    _write_json(
        graph_root / "test" / case_id / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "hsf_threadpool_busy",
                    "label": "provider-app THREADPOOL_BUSY",
                    "score": 8.0,
                    "reason": "HSF-0002 THREADPOOL_BUSY provider thread pool is full",
                }
            ],
            "evidence": [
                {"name": "trace_get", "summary": "HSFTimeOutException THREADPOOL_BUSY"},
                {"name": "metric_hsf_rt", "summary": "provider error qps rising"},
            ],
        },
    )

    report = build_frontier_report(
        baseline_path=baseline,
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
        validation_memory_path=memory,
    )
    case = report.cases[0]

    assert case.bucket == "hsf_counterfactual_review"
    assert "hsf_frontier" in case.signals
    assert "low_baseline_support" in case.signals


def test_render_frontier_markdown_and_cli_write_outputs(tmp_path) -> None:
    case_id = _case_id("21ff")
    graph_root = tmp_path / "graphs"
    baseline = tmp_path / "baseline.json"
    memory = tmp_path / "memory.json"
    _memory(memory)
    _write_json(
        baseline,
        {
            "results": [
                _baseline_row(case_id, "根因：resource_lock_setting_his 慢 SQL。"),
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
                    "label": "resource_lock_setting_his slow SQL",
                    "score": 8.0,
                    "reason": "TDDL_QUERY duration=2646ms",
                }
            ],
            "evidence": [{"name": "rds_sql_detail", "summary": "slow SQL full scan"}],
        },
    )
    out_json = tmp_path / "frontier.json"
    out_md = tmp_path / "frontier.md"

    assert (
        main(
            [
                "frontier",
                "--baseline",
                str(baseline),
                "--graph-root",
                str(graph_root),
                "--dataset-dir",
                str(tmp_path / "missing-dataset"),
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
    markdown = render_frontier_markdown(
        build_frontier_report(
            baseline_path=baseline,
            graph_roots=[graph_root],
            split="test",
            dataset_dir=tmp_path / "missing-dataset",
            validation_memory_path=memory,
        )
    )
    assert payload["cases"][0]["case_id"] == case_id
    assert "RealRCA Experiment Frontier" in out_md.read_text(encoding="utf-8")
    assert "Ranked Frontier" in markdown
