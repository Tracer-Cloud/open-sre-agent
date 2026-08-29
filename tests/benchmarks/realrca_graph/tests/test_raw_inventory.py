from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.raw_inventory import (
    build_raw_inventory_report,
    render_raw_inventory_markdown,
)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_raw_inventory_marks_non_ingested_raw_mechanism(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "sls_app_order_THREADPOOL_BUSY.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：order-service HSF 调用超时。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(tmp_path / "dataset" / "test.json", [{"case_id": case_id, "type": "HSF"}])
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "HSF"},
            "root_candidates": [{"kind": "trace_span", "label": "order-service", "score": 4.0}],
            "evidence": [
                {
                    "name": "alarm_get",
                    "raw_path": str(case_dir / "raw" / "alarm_get.json"),
                    "summary": "alarm app=order-service",
                }
            ],
        },
    )
    _write_json(case_dir / "raw" / "alarm_get.json", {"result": {"app": "order-service"}})
    _write_json(
        raw_path,
        [
            {
                "message": "HSF-0002 THREADPOOL_BUSY HSFTimeOutException provider_ip=33.62.98.154",
                "traceId": "212a6a3417840231458777961e0d45",
            }
        ],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "dataset",
    )

    case = report.cases[0]
    assert case.case_id == case_id
    assert "raw_mechanism_uncovered:hsf_threadpool_busy" in case.categories
    assert "nonempty_raw_not_referenced:sls_app" in case.categories
    assert case.top_files[0].path == str(raw_path)
    assert "补 raw 解析/ontology 覆盖" in case.recommended_actions[0]


def test_raw_inventory_splits_metaq_broker_failure_from_business_failure(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321bc"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "log_error_list.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：Java 异常错误数升高。",
                    "trace_id": "213e07cd17864976509897160e1238",
                }
            ]
        },
    )
    _write_json(tmp_path / "dataset" / "test.json", [{"case_id": case_id, "type": "HSF"}])
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "HSF"},
            "root_candidates": [
                {"kind": "log_error", "label": "HSFTimeOutException", "score": 4.0}
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "raw_path": str(case_dir / "raw" / "alarm_get.json"),
                    "summary": "alarm app=idle-cco",
                }
            ],
        },
    )
    _write_json(case_dir / "raw" / "alarm_get.json", {"result": {"app": "idle-cco"}})
    _write_json(
        raw_path,
        {
            "errors": [
                {
                    "message": (
                        "RocketmqCommon fetch name server address exception; "
                        "MQClientException: broker[trade_sub_notify_metaq-zoneB-11] not exist"
                    )
                }
            ]
        },
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "dataset",
    )

    case = report.cases[0]
    assert "metaq_broker_failure" in case.raw_mechanisms
    assert "raw_mechanism_uncovered:metaq_broker_failure" in case.categories


def test_raw_inventory_detects_auth_failure_in_trace_raw(tmp_path) -> None:
    case_id = "01a0330f-2efc-7f72-bbd5-a3c1d5dc1d79"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "trace_list_server_app_exact.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：goc-pass 代理返回 401。",
                    "trace_id": "8ccd75d217815846928741544e77e6",
                }
            ]
        },
    )
    _write_json(tmp_path / "dataset" / "test.json", [{"case_id": case_id, "type": "自定义监控"}])
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "自定义监控"},
            "root_candidates": [],
            "evidence": [],
        },
    )
    _write_json(
        raw_path,
        [
            {
                "server_name": "goc-pass:goc-passhost",
                "service": "https://tr.alibaba-inc.com/gocFaultDef/innerApi/v2/incident/scenarios/level/defs",
                "result_code": 401,
                "result_type": 3,
            }
        ],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "dataset",
    )

    case = report.cases[0]
    assert "auth_failure" in case.raw_mechanisms
    assert "raw_mechanism_uncovered:auth_failure" in case.categories


def test_raw_inventory_does_not_flag_mechanism_already_in_graph(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321bb"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "sls_app_order_THREADPOOL_BUSY.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：provider 线程池打满。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "hsf_threadpool_busy",
                    "label": "THREADPOOL_BUSY:33.62.98.154",
                    "score": 5.0,
                    "reason": "HSFTimeOutException from provider app log",
                }
            ],
            "evidence": [
                {
                    "name": "sls_app_order_THREADPOOL_BUSY",
                    "raw_path": str(raw_path),
                    "summary": "THREADPOOL_BUSY HSFTimeOutException provider_ip=33.62.98.154",
                }
            ],
        },
    )
    _write_json(
        raw_path,
        [{"message": "THREADPOOL_BUSY HSFTimeOutException provider_ip=33.62.98.154"}],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "raw_mechanism_uncovered" not in case.categories
    assert case.uncovered_mechanisms == []
    assert case.referenced_raw_files == 1


def test_raw_inventory_requires_fault_word_for_cache_timeout(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321cc"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "trace_list_client_app_exact.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：业务下游异常。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "Tair"},
            "root_candidates": [],
            "evidence": [{"name": "trace_list_client_app_exact", "raw_path": str(raw_path)}],
        },
    )
    _write_json(raw_path, [{"service": "redis-cache", "result": "ok", "duration": 5}])

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "cache_timeout" not in case.raw_mechanisms
    assert case.uncovered_mechanisms == []


def test_raw_inventory_demotes_trace_sql_sidecar_for_cache_case(tmp_path) -> None:
    case_id = "01a0330f-2efc-7f72-bbd5-a3c1d5dc1d71"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "trace_get_with_side_sql.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": (
                        "根因：RedisCacheManager.batchReadFromRedis 调用 Jedis.mget "
                        "发生 Socket Read timed out。"
                    ),
                    "trace_id": "21030cd817844920277998294e1127",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "Tair"},
            "root_candidates": [
                {
                    "kind": "pattern_cache_timeout",
                    "label": "cache_timeout",
                    "score": 6.0,
                    "reason": "JedisConnectionException Read timed out",
                }
            ],
            "evidence": [
                {"name": "log_error_list", "summary": "JedisConnectionException Read timed out"}
            ],
        },
    )
    _write_json(
        raw_path,
        [
            {
                "summary": (
                    "sampled trace also contains unrelated SQL spans: "
                    "TDDL_QUERY@global_uic_ae_0007:global_user\\u001ae6c547cf "
                    "duration_ms=3000 and unique_key text in side branch"
                )
            }
        ],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "slow_sql" in case.raw_mechanisms
    assert "duplicate_key" in case.raw_mechanisms
    assert case.uncovered_mechanisms == []
    assert "raw_mechanism_uncovered" not in case.categories
    assert "sidecar_raw_mechanism:slow_sql" in case.categories
    assert "sidecar_raw_mechanism:duplicate_key" in case.categories


def test_raw_inventory_keeps_trace_sql_gap_for_database_case(tmp_path) -> None:
    case_id = "01a0330f-2efc-7f72-bbd5-a3c1d5dc1d62"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "trace_get_sql.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：接口超时，待定位 SQL。",
                    "trace_id": "21030cd817844920277998294e1127",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "TDDL"},
            "root_candidates": [],
            "evidence": [],
        },
    )
    _write_json(
        raw_path,
        [{"summary": "TDDL_QUERY@trade_db_0001:trade_order\\u001aabc123 duration_ms=3000"}],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "slow_sql" in case.uncovered_mechanisms
    assert "raw_mechanism_uncovered:slow_sql" in case.categories


def test_raw_inventory_ignores_inactive_node_health_events(tmp_path) -> None:
    case_id = "01a0330f-2efc-7f72-bbd5-a3c1d5dc1d90"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "event_query_app.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：trade-contract MetaQ 消费倾斜导致 CPU 升高。",
                    "trace_id": "0babee3317864164187833687d03db",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "CPU"},
            "root_candidates": [],
            "evidence": [],
        },
    )
    _write_json(
        raw_path,
        [
            {
                "stream": {
                    "type": "Node.CPUPressure",
                    "appName": '["trade-contract","phyhost-ecs-ali"]',
                },
                "values": [
                    [
                        1786414703,
                        {
                            "type": "Node.CPUPressure",
                            "Data": {"Status": "False", "Message": "load is normal"},
                        },
                    ]
                ],
            },
            {
                "stream": {
                    "type": "Kernel.OOMKilling",
                    "appName": '["trade-contract","phyhost-ecs-ali"]',
                },
                "values": [
                    [
                        1786414703,
                        {
                            "type": "Kernel.OOMKilling",
                            "Data": {"Status": "False", "Message": "nothing oom"},
                        },
                    ]
                ],
            },
        ],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "infra_event" not in case.raw_mechanisms
    assert "pod_event" not in case.raw_mechanisms
    assert case.uncovered_mechanisms == []


def test_raw_inventory_demotes_unrelated_app_pod_event(tmp_path) -> None:
    case_id = "01a0330f-2efc-7f72-bbd5-a3c1d5dc1d90"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "event_changefree_query_buyeragent_user.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：trade-contract MetaQ 消费倾斜导致 CPU 升高。",
                    "trace_id": "0babee3317864164187833687d03db",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "CPU"},
            "root_candidates": [],
            "evidence": [{"name": "event_changefree_query", "summary": "changefree event covered"}],
        },
    )
    _write_json(
        raw_path,
        [
            {
                "stream": {
                    "appName": '["buyeragent-user"]',
                    "source": "changefree",
                    "type": "EXE[cf:normandy-director]",
                },
                "values": [
                    [
                        1786418085,
                        {
                            "change_object": '{"appName":"buyeragent-user"}',
                            "change_summary": "buyeragent-user 应用变更",
                            "change_system": "normandy-director",
                            "detailUrl": (
                                "https://n.alibaba-inc.com/micro/ops/app/"
                                "buyeragent-user/action/pod-eviction/detail"
                            ),
                        },
                    ]
                ],
            }
        ],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "pod_event" in case.raw_mechanisms
    assert case.uncovered_mechanisms == []
    assert "raw_mechanism_uncovered:pod_event" not in case.categories
    assert "sidecar_raw_mechanism:pod_event" in case.categories


def test_raw_inventory_does_not_flag_business_specs_text_as_infra_event(tmp_path) -> None:
    case_id = "01a0330f-2efc-7f72-bbd5-a3c1d5dc1d90"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "log_error_list.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：trade-contract MetaQ 消费倾斜导致 CPU 升高。",
                    "trace_id": "0babee3317864164187833687d03db",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "CPU"},
            "root_candidates": [],
            "evidence": [],
        },
    )
    _write_json(
        raw_path,
        {
            "errors": [
                {
                    "message": (
                        "failed to acquire lock, lockKey=communication_node_process_48050501, "
                        "nodeDataKey=PRODUCT_DYNAMIC_SPECS"
                    )
                }
            ]
        },
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "infra_event" not in case.raw_mechanisms
    assert "raw_mechanism_uncovered:infra_event" not in case.categories


def test_raw_inventory_detects_connection_pool_failure(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321ee"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "sls_app_db_connection.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：数据库访问失败。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "TDDL"},
            "root_candidates": [],
            "evidence": [],
        },
    )
    _write_json(
        raw_path,
        [
            {
                "message": (
                    "DruidDataSource get connection timeout from TDDL_CONN; "
                    "maxActive reached on host 33.70.176.208"
                )
            }
        ],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "connection_pool" in case.raw_mechanisms
    assert "raw_mechanism_uncovered:connection_pool" in case.categories


def test_raw_inventory_does_not_flag_plain_druid_statement_stack_as_connection_pool(
    tmp_path,
) -> None:
    case_id = "01a0330f-2efc-7f72-bbd5-a3c1d5dc1d7a"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "sls_app_finance_sql_error.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：AWP 合同产品信息查询返回业务失败。",
                    "trace_id": "0bf8dd4417818866557414720e0c03",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "自定义监控"},
            "root_candidates": [],
            "evidence": [],
        },
    )
    _write_json(
        raw_path,
        [
            {
                "message": (
                    "org.springframework.dao.DataIntegrityViolationException: insert failed\n"
                    "at com.alibaba.druid.filter.FilterChainImpl.preparedStatement_executeUpdate\n"
                    "at com.alibaba.druid.pool.DruidPooledPreparedStatement.executeUpdate"
                )
            }
        ],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "connection_pool" not in case.raw_mechanisms
    assert "raw_mechanism_uncovered:connection_pool" not in case.categories


def test_raw_inventory_detects_metaq_duplicate_update_conflict(tmp_path) -> None:
    case_id = "01a0330f-2efc-7f72-bbd5-a3c1d5dc1d68"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    raw_path = case_dir / "raw" / "sls_app_metaq.json"
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：消息消费失败。",
                    "trace_id": "2150466d17806513712328452e0c86",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "METAQ"},
            "root_candidates": [],
            "evidence": [{"name": "sls_app_metaq", "raw_path": str(raw_path)}],
        },
    )
    _write_json(
        raw_path,
        [
            {
                "content": (
                    "ConsumeMessageThread UPDATE_ERROR 更新失败 "
                    "ServiceOrderTunnelImpl.updateWithVersion "
                    "LOGISTICS_ON_DEMAND_TRACE_TOPIC mailNo=YT1134183699405"
                )
            }
        ],
    )

    report = build_raw_inventory_report(
        baseline_path=tmp_path / "baseline.json",
        graph_roots=[graph_root],
        split="test",
        dataset_dir=tmp_path / "missing-dataset",
    )

    case = report.cases[0]
    assert "mq_duplicate_conflict" in case.raw_mechanisms
    assert "mq_duplicate_conflict" in case.uncovered_mechanisms


def test_render_raw_inventory_markdown_and_cli_write_outputs(tmp_path) -> None:
    case_id = "case-a"
    graph_root = tmp_path / "graphs"
    case_dir = graph_root / "test" / case_id
    _write_json(
        tmp_path / "baseline.json",
        {
            "results": [
                {
                    "case_id": case_id,
                    "diagnosis_output": "根因：SQL 慢查询。",
                    "trace_id": "212a6a3417840231458777961e0d45",
                }
            ]
        },
    )
    _write_json(
        case_dir / "graph_context.json",
        {
            "case": {"case_id": case_id, "split": "test", "type": "TDDL"},
            "root_candidates": [],
            "evidence": [],
        },
    )
    _write_json(
        case_dir / "raw" / "rds_sql_full_rm-test.json",
        {"result": [{"SQL_ID": "abc", "sql": "select * from t"}]},
    )
    out_json = tmp_path / "raw-inventory.json"
    out_md = tmp_path / "raw-inventory.md"

    assert (
        main(
            [
                "raw-inventory",
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
    markdown = render_raw_inventory_markdown(
        build_raw_inventory_report(
            baseline_path=tmp_path / "baseline.json",
            graph_roots=[graph_root],
            split="test",
            dataset_dir=tmp_path / "missing-dataset",
        )
    )
    assert payload["cases"][0]["case_id"] == case_id
    assert "RealRCA Raw Evidence Inventory" in out_md.read_text(encoding="utf-8")
    assert "rds_sql" in markdown
