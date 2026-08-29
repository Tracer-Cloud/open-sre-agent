from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.graph_analogues import (
    build_graph_analogue_report,
    graph_analogues_for_prompt,
    load_graph_case_profiles,
)
from tests.benchmarks.realrca_graph.graph_store import index_resolved_graphs


def _write_graph(
    root: Path,
    case_id: str,
    *,
    split: str = "test",
    case_type: str,
    app: str,
    root_kind: str,
    root_label: str,
    evidence_name: str,
    evidence_summary: str,
) -> None:
    graph_dir = root / split / case_id
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph_context.json").write_text(
        json.dumps(
            {
                "case": {"split": split, "case_id": case_id, "type": case_type},
                "nodes": [
                    {
                        "id": f"app:{app}",
                        "kind": "app",
                        "label": app,
                        "props": {"role": "consumer"},
                    },
                    {
                        "id": f"service:{app}.ProviderApi",
                        "kind": "service",
                        "label": f"com.demo.{app}.ProviderApi:query",
                    },
                ],
                "edges": [
                    {
                        "source": f"app:{app}",
                        "rel": "CALLS",
                        "target": f"service:{app}.ProviderApi",
                    }
                ],
                "evidence": [
                    {
                        "name": evidence_name,
                        "command": evidence_name,
                        "returncode": 0,
                        "summary": evidence_summary,
                    }
                ],
                "root_candidates": [
                    {
                        "kind": root_kind,
                        "label": root_label,
                        "score": 8.0,
                        "reason": evidence_summary,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_graph_analogues_prefer_mechanism_and_root_kind_over_entity_overlap(tmp_path: Path) -> None:
    root = tmp_path / "graphs"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(
        root,
        "case-query",
        case_type="HSF",
        app="checkout-app",
        root_kind="pattern_limit",
        root_label="checkout provider Sentinel limit",
        evidence_name="metric_hsf_provider_error_qps",
        evidence_summary="HSF provider SentinelBlockException error qps spike",
    )
    _write_graph(
        root,
        "case-limit",
        case_type="HSF",
        app="payment-app",
        root_kind="pattern_limit",
        root_label="payment provider Sentinel limit",
        evidence_name="trace_get",
        evidence_summary="HSF provider SentinelBlockException timeout",
    )
    _write_graph(
        root,
        "case-same-app-sql",
        case_type="TDDL",
        app="checkout-app",
        root_kind="pattern_slow_sql",
        root_label="orders slow SQL",
        evidence_name="diagnose_rds_sql",
        evidence_summary="checkout-app TDDL_QUERY orders slow sql",
    )
    index_resolved_graphs(
        [root],
        graph_label="latest-test-resolved",
        db_path=db_path,
        split="test",
    )

    report = build_graph_analogue_report(
        db_path=db_path,
        split="test",
        query_graph_label="latest-test-resolved",
        case_ids=["case-query"],
        match_limit=2,
    )

    matches = report.cases[0].matches
    assert matches[0].case_id == "case-limit"
    assert "limit" in matches[0].matched_mechanisms
    assert "pattern_limit" in matches[0].matched_root_kinds
    assert matches[0].similarity > matches[1].similarity


def test_graph_analogues_match_provider_error_qps_soft_mechanism(tmp_path: Path) -> None:
    root = tmp_path / "graphs"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(
        root,
        "case-query",
        case_type="HSF",
        app="mx-project",
        root_kind="pattern_hsf_provider_error_qps_spike",
        root_label="mx-projecthost ProjectCenterService.getProjectStructuredInfo provider_error_qps_spike",
        evidence_name="metric_middleware_hsf_provider_service_method_error_qps",
        evidence_summary="middleware_hsf_provider_service_method_error_qps provider error_qps rising",
    )
    _write_graph(
        root,
        "case-match",
        case_type="HSF",
        app="tariffcode",
        root_kind="pattern_hsf_provider_error_qps_spike",
        root_label="tariffcodehost TaxCalApplicationService.taxCalForItem provider_error_qps_spike",
        evidence_name="metric_middleware_hsf_provider_service_method_error_qps",
        evidence_summary="middleware_hsf_provider_service_method_error_qps hsf provider success_rate drop",
    )
    _write_graph(
        root,
        "case-sql",
        case_type="TDDL",
        app="mx-project",
        root_kind="pattern_slow_sql",
        root_label="orders slow SQL",
        evidence_name="diagnose_rds_sql",
        evidence_summary="orders slow sql",
    )
    index_resolved_graphs(
        [root],
        graph_label="latest-test-resolved",
        db_path=db_path,
        split="test",
    )

    report = build_graph_analogue_report(
        db_path=db_path,
        split="test",
        query_graph_label="latest-test-resolved",
        case_ids=["case-query"],
        match_limit=2,
    )

    match = report.cases[0].matches[0]

    assert match.case_id == "case-match"
    assert "provider_error_qps" in match.matched_mechanisms
    assert "pattern_hsf_provider_error_qps_spike" in match.matched_root_kinds


def test_graph_analogues_match_custom_monitor_business_metric(tmp_path: Path) -> None:
    root = tmp_path / "graphs"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(
        root,
        "case-query",
        case_type="自定义监控",
        app="goc-pass",
        root_kind="custom_monitor_signal",
        root_label="1026_SPM_19:失败数:代理名=gocBlockout",
        evidence_name="metric_custom_1026_spm_19_失败数",
        evidence_summary="custom_monitor 业务指标 失败数 max=66 trend=rising",
    )
    _write_graph(
        root,
        "case-match",
        case_type="自定义监控",
        app="risk-proxy",
        root_kind="custom_monitor_signal",
        root_label="1001_SPM_8:失败数:代理名=apiA",
        evidence_name="metric_custom_1001_spm_8_失败数",
        evidence_summary="custom_monitor 业务指标 失败数 max=42 trend=rising",
    )
    _write_graph(
        root,
        "case-sql",
        case_type="TDDL",
        app="goc-pass",
        root_kind="pattern_slow_sql",
        root_label="orders slow SQL",
        evidence_name="diagnose_rds_sql",
        evidence_summary="orders slow sql",
    )
    index_resolved_graphs(
        [root],
        graph_label="latest-test-resolved",
        db_path=db_path,
        split="test",
    )

    report = build_graph_analogue_report(
        db_path=db_path,
        split="test",
        query_graph_label="latest-test-resolved",
        case_ids=["case-query"],
        match_limit=2,
    )

    match = report.cases[0].matches[0]

    assert match.case_id == "case-match"
    assert "business_metric" in match.matched_mechanisms
    assert "custom_monitor_signal" in match.matched_root_kinds


def test_graph_analogues_extract_evidence_mechanism_and_downgrade_empty_match(
    tmp_path: Path,
) -> None:
    root = tmp_path / "graphs"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(
        root,
        "case-query",
        case_type="TDDL",
        app="checkout-app",
        root_kind="trace_span",
        root_label="checkout write latency",
        evidence_name="metric_middleware_tddl_write_rt",
        evidence_summary=(
            "middleware_tddl_write_rt TDDL_QUERY@db:resource_lock_setting_his "
            "sql_table=resource_lock_setting_his duration_ms=2646"
        ),
    )
    _write_graph(
        root,
        "case-same-app-generic",
        case_type="TDDL",
        app="checkout-app",
        root_kind="trace_span",
        root_label="checkout generic provider path",
        evidence_name="trace_get",
        evidence_summary="checkout-app ordinary provider path without exception",
    )
    _write_graph(
        root,
        "case-sql",
        case_type="TDDL",
        app="catalog-app",
        root_kind="trace_span",
        root_label="catalog write latency",
        evidence_name="diagnose_rds_sql",
        evidence_summary=(
            "tddl_write_rt TDDL_QUERY@db:resource_lock_setting_his slow sql "
            "sql_table=resource_lock_setting_his"
        ),
    )
    index_resolved_graphs(
        [root],
        graph_label="latest-test-resolved",
        db_path=db_path,
        split="test",
    )

    report = build_graph_analogue_report(
        db_path=db_path,
        split="test",
        query_graph_label="latest-test-resolved",
        case_ids=["case-query"],
        match_limit=2,
    )

    case = report.cases[0]
    assert "sql" in case.profile.mechanisms
    matches = {match.case_id: match for match in case.matches}
    assert case.matches[0].case_id == "case-sql"
    assert matches["case-sql"].matched_mechanisms == ["sql"]
    assert matches["case-same-app-generic"].matched_mechanisms == []
    assert matches["case-same-app-generic"].similarity <= 0.35

    snippets = graph_analogues_for_prompt(report, limit=2)[case.case_id]
    assert snippets[0]["analogue_role"] == "supporting_analogue"
    assert any(item["analogue_role"] == "negative_constraint" for item in snippets)


def test_graph_analogue_profile_includes_high_score_derived_roots(tmp_path: Path) -> None:
    root = tmp_path / "graphs"
    graph_dir = root / "test" / "case-drop"
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph_context.json").write_text(
        json.dumps(
            {
                "case": {
                    "split": "test",
                    "case_id": "case-drop",
                    "type": "OTHER",
                    "input": "机器存活数量同比下跌 appGroup=mtee3.cn.prodhost",
                },
                "nodes": [],
                "edges": [],
                "evidence": [
                    {
                        "name": "event_change_list",
                        "command": "sf event change list --app mtee3 --infra -f json",
                        "summary": {
                            "business_changes": [
                                {
                                    "id": "2843585453",
                                    "change_type": "OFFLINE_HOST",
                                    "title": "正式-机器下线",
                                    "result": "变更成功",
                                    "system": "normandy-director",
                                    "end_time": "2026-06-11 22:20:36",
                                }
                            ]
                        },
                    }
                ],
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": f"side-span-{index}",
                        "score": 4.0,
                    }
                    for index in range(12)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "graphs.sqlite"
    index_resolved_graphs([root], graph_label="latest-test-resolved", db_path=db_path, split="test")

    profile = load_graph_case_profiles(
        db_path=db_path,
        split="test",
        graph_labels=["latest-test-resolved"],
    )[0]

    assert "pattern_instance_count_drop_offline_change" in profile.root_kinds
    assert profile.root_labels[0] == "mtee3 change_id=2843585453 normandy_offline_capacity_drop"
    assert {"change", "host"} <= set(profile.mechanisms)


def test_graph_analogues_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    root = tmp_path / "graphs"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(
        root,
        "case-query",
        case_type="Tair",
        app="cart-app",
        root_kind="pattern_cache_timeout",
        root_label="redis r-abc timeout",
        evidence_name="trace_get",
        evidence_summary="JedisConnectionException redis timeout",
    )
    _write_graph(
        root,
        "case-match",
        case_type="Tair",
        app="item-app",
        root_kind="pattern_cache_timeout",
        root_label="redis r-def timeout",
        evidence_name="metric_tair_rt",
        evidence_summary="redis cache timeout rt rising",
    )
    index_resolved_graphs(
        [root],
        graph_label="latest-test-resolved",
        db_path=db_path,
        split="test",
    )
    out_json = tmp_path / "analogues.json"
    out_md = tmp_path / "analogues.md"

    assert (
        main(
            [
                "graph-analogues",
                "--db",
                str(db_path),
                "--query-label",
                "latest-test-resolved",
                "--case-id",
                "case-query",
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["cases"][0]["matches"][0]["case_id"] == "case-match"
    assert "RealRCA Graph Analogues" in out_md.read_text(encoding="utf-8")


def test_graph_analogues_can_search_public_validation_split(tmp_path: Path) -> None:
    test_root = tmp_path / "test-graphs"
    validation_root = tmp_path / "validation-graphs"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(
        test_root,
        "case-query",
        case_type="HSF",
        app="buyer-app",
        root_kind="pattern_limit",
        root_label="buyer provider Sentinel limit",
        evidence_name="metric_hsf_provider_error_qps",
        evidence_summary="HSF provider SentinelBlockException",
    )
    _write_graph(
        validation_root,
        "case-public",
        split="validation",
        case_type="HSF",
        app="public-app",
        root_kind="pattern_limit",
        root_label="public provider Sentinel limit",
        evidence_name="trace_get",
        evidence_summary="HSF provider SentinelBlockException",
    )
    index_resolved_graphs(
        [test_root],
        graph_label="latest-test-resolved",
        db_path=db_path,
        split="test",
    )
    index_resolved_graphs(
        [validation_root],
        graph_label="latest-validation-resolved",
        db_path=db_path,
        split="validation",
    )

    report = build_graph_analogue_report(
        db_path=db_path,
        split="test",
        query_graph_label="latest-test-resolved",
        search_graph_labels=["latest-validation-resolved"],
        search_splits=["validation"],
        case_ids=["case-query"],
    )

    assert report.search_splits == ["validation"]
    assert report.cases[0].matches[0].case_id == "case-public"
    assert report.cases[0].matches[0].split == "validation"
