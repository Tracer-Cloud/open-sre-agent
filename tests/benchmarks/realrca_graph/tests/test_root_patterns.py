from __future__ import annotations

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.features import infer_root_layer
from tests.benchmarks.realrca_graph.root_patterns import pattern_root_candidates


def test_pattern_candidates_extract_slow_sql_from_visible_text() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "自定义监控"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": (
                        "慢SQL SELECT distinct(hu_code) FROM linehaul_inbound_abnormal_record "
                        "WHERE warehouse_id=? AND abnormal_status=?"
                    ),
                }
            ],
            "root_candidates": [],
        }
    )

    assert candidates
    assert candidates[0]["kind"] == "pattern_slow_sql"
    assert candidates[0]["label"] == "linehaul_inbound_abnormal_record"


def test_pattern_candidate_can_precede_unrelated_event_candidate() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "OTHER"},
            "root_candidates": [
                {
                    "kind": "event",
                    "label": "opaque-event-id",
                    "score": 5.0,
                    "reason": "infrastructure event near alarm",
                }
            ],
            "evidence": [
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app demo -f json",
                    "summary": (
                        "Redis instance r-8vb219d10038c044 query timeout caused "
                        "getOrderDetailV2 exception"
                    ),
                }
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "pattern_cache_timeout"
    assert bundle.hypotheses[0].label == "r-8vb219d10038c044"
    assert bundle.hypotheses[0].root_layer == "cache"


def test_pattern_candidates_extract_heimdall_mtop_security_scan() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "自定义监控"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "tmc-datacube:tmc-datacubehost",
                    "reason": "UnsupportedOperationException",
                    "props": {
                        "user_data": (
                            "heimdall=1 @s0=http://acs.m.taobao.com/h5/"
                            "mtop.com.alibaba.datacube.api.star.getqnmobilepop/1.0/"
                        )
                    },
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_security_scan"
    assert candidates[0]["label"] == "mtop security_scan"


def test_pattern_candidates_extract_security_fourier_x5action() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "自定义监控"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "(jedis@cdata-redis-zb.alibaba-inc.com:6379)",
                    "props": {
                        "client": "security-fourier:security-fourierhost",
                        "user_data": "bx-x5action=break @s0=FOURIER_CHECK_GRPC",
                    },
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_security_scan"
    assert candidates[0]["label"] == "security-fourier security_scan"


def test_security_fourier_pipeline_check_without_attack_action_is_not_scan() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "自定义监控"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "(jedis@r-8vbhsxsii2vswr9bj2.redis.zhangbei.rds.aliyuncs.com:6379)",
                    "reason": "abnormal trace span",
                    "props": {
                        "client": "security-fourier:security-fourierhost",
                        "service": "PIPELINESYNC:r-8vbhsxsii2vswr9bj2.redis.zhangbei.rds.aliyuncs.com:6379",
                        "result_code": "00",
                        "user_data": "@s0=FOURIER_CHECK_GRPC bx-uuid=4de4eefbf54ef72839cf8b75176d5b24",
                    },
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_security_scan" for item in candidates)


def test_security_fourier_alone_does_not_create_security_candidate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "security-fourier:security-fourierhost",
                    "reason": "ordinary cache lookup finished without attack markers",
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_security_scan" for item in candidates)


def test_mtop_security_fourier_without_probe_action_does_not_create_security_candidate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "Tair"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "security-fourier:security-fourierhost",
                    "reason": "security-fourier calls mtop as a normal side request",
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_security_scan" for item in candidates)


def test_tair_case_ignores_probe_only_security_side_span() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "Tair"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "security-fourier:security-fourierhost",
                    "reason": "security-fourier bx-x5action=break FOURIER_CHECK_GRPC side span near Redis",
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_security_scan" for item in candidates)


def test_tddl_case_ignores_probe_only_security_side_span() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "TDDL"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "security-fourier:security-fourierhost",
                    "reason": "security-fourier bx-x5action=break FOURIER_CHECK_GRPC side span near DB",
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_security_scan" for item in candidates)


def test_tddl_case_keeps_mtop_security_scan_signal() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "TDDL"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "mtop gateway request",
                    "reason": "mtop bizType carries heimdall malicious payload",
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_security_scan"
    assert candidates[0]["label"] == "mtop security_scan"


def test_mtop_rasp_attack_log_creates_security_context_candidate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "自定义监控"},
            "evidence": [
                {
                    "name": "log_error_list",
                    "summary": (
                        "java.lang.RuntimeException caused by com.alibaba.fastjson.JSONException "
                        "and java.lang.SecurityException: RASP has block a real attack"
                    ),
                }
            ],
            "nodes": [
                {"kind": "app", "label": "mtop"},
                {"kind": "app", "label": "security-fourier"},
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_security_scan"
    assert candidates[0]["label"] == "mtop security_scan"
    assert candidates[0]["props"]["security_context"] is True


def test_hsf_case_ignores_cross_context_mtop_rasp_security_noise() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=tripps metric=middleware_hsf_provider_success_rate",
                },
                {
                    "name": "log_error_list",
                    "summary": (
                        "java.lang.RuntimeException caused by com.alibaba.fastjson.JSONException "
                        "and java.lang.SecurityException: RASP has block a real attack"
                    ),
                },
            ],
            "nodes": [
                {"kind": "app", "label": "mtop"},
                {"kind": "app", "label": "security-fourier"},
            ],
        }
    )

    assert all(item["kind"] != "pattern_security_scan" for item in candidates)


def test_config_mq_failure_ignores_unrelated_mq_trace_without_metaq_context() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "自定义监控"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": (
                        "alarm app=tmc-datacube metric=19_generalComp_189 "
                        "hsf provider success rate falling"
                    ),
                },
                {
                    "name": "event_changefree_query",
                    "summary": (
                        "events count=1 change_system=aone change_type=CONFIG_PUSH "
                        "change_app=recommend-pro-max dataId=result.notice.config "
                        "crIds=34475993"
                    ),
                },
                {
                    "name": "trace_get",
                    "summary": (
                        "trace spans=400 top=service=MQRecv@CARGO_FULL_LINK_CHECK_TASK_HIGH_PRIORITY_TOPIC "
                        "result=1/BIZ_ERROR"
                    ),
                },
            ],
        }
    )

    assert all(item["kind"] != "pattern_config_mq_failure" for item in candidates)


def test_tddl_security_scan_write_failure_creates_conflict_candidate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "TDDL"},
            "evidence": [
                {
                    "kind": "trace_span",
                    "summary": (
                        "trace spans=8 top=http://h5api.m.taobao.com/h5/mtop.demo.ask/1.0/ "
                        "heimdall=1 sql_top=service=TDDL_INSERT@demo_unit:"
                        "robotx_chat_log\x1a35978e7c result=1"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_security_sql_conflict"
    assert top["label"] == "robotx_chat_log unique_key_conflict"
    assert top["props"]["write_failure"] is True


def test_tddl_security_scan_successful_write_does_not_create_conflict_candidate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "TDDL"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "mtop gateway request",
                    "reason": "http://h5api.m.taobao.com/h5/mtop.demo.ask/1.0/ heimdall=1",
                },
                {
                    "kind": "trace_span",
                    "label": "(db@demo_unit)",
                    "props": {
                        "service": "TDDL_INSERT@demo_unit:robotx_chat_log\x1a35978e7c",
                        "result_code": "00",
                    },
                },
            ],
        }
    )

    assert all(item["kind"] != "pattern_security_sql_conflict" for item in candidates)


def test_pattern_candidates_extract_sentinel_limit_from_error_summary() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "log_error_list",
                    "summary": (
                        "exceptions={'SentinelBlockException': 9, "
                        "'com.alibaba.csp.sentinel.slots.block.BlockException': 3} "
                        "tables={'COM.ALIBABA.DATACUBE.SERVICE.PRICE.SERVICE.PRICEQUERYSERVICE': 9}"
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_limit"
    assert candidates[0]["label"] == "com.alibaba.datacube.service.price.service.pricequeryservice"


def test_hsf_offline_change_creates_capacity_change_candidate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=1 "
                        "top=[app_group=cngdchost,service=com.cainiao.global.RouteLineService:1.0.0.offline,"
                        "method=routeLine~CC min=1073,max=208900,avg=94490,last=1073,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "summary": (
                        "trace top=client=cngdc:cngdchost server=cnexport-cb-route:"
                        "cnexport-cb-route_offline_host service=com.cainiao.global.RouteLineService:"
                        "1.0.0.offline@routeLine~CC duration_ms=28075 result=03/TIMEOUT"
                    ),
                },
                {"name": "event_change_list", "summary": "changes=3 top=id=2909444204"},
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_capacity_change"
    assert top["label"].startswith("cnexport-cb-route:com.cainiao.global.RouteLineService")
    assert top["props"]["capacity_change"] is True


def test_hsf_offline_without_change_does_not_create_capacity_change_candidate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=1 "
                        "top=[app_group=cngdchost,service=com.cainiao.global.RouteLineService:1.0.0.offline,"
                        "method=routeLine~CC min=1073,max=208900,avg=94490,last=1073,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "summary": (
                        "trace top=client=cngdc:cngdchost server=cnexport-cb-route:"
                        "cnexport-cb-route_offline_host service=com.cainiao.global.RouteLineService:"
                        "1.0.0.offline@routeLine~CC duration_ms=28075 result=03/TIMEOUT"
                    ),
                },
            ],
        }
    )

    assert all(item["kind"] != "pattern_capacity_change" for item in candidates)


def test_instance_count_drop_offline_change_pattern_extracts_normandy_root() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "OTHER"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": (
                        "alarm appGroup=mtee3.cn.prodhost metric=cnt "
                        "机器数量 当前值为:60 同比下跌:83.333%"
                    ),
                },
                {
                    "name": "event_change_list",
                    "summary": (
                        "changes=77 top=id=2843585453 system=normandy-director "
                        "type=OFFLINE_HOST title=正式-机器下线 result=变更成功 "
                        "time=2026-06-11 22:20:36"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_instance_count_drop_offline_change"
    assert top["label"] == "mtee3 change_id=2843585453 normandy_offline_capacity_drop"
    assert top["props"]["change_system"] == "normandy-director"
    assert infer_root_layer(top["kind"], top["label"], top["props"], top["reason"]) == "change"


def test_instance_count_drop_offline_change_pattern_requires_count_drop_symptom() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "OTHER"},
            "evidence": [
                {
                    "name": "event_change_list",
                    "summary": (
                        "changes=1 top=id=2843585453 system=normandy-director "
                        "type=OFFLINE_HOST title=正式-机器下线 result=变更成功"
                    ),
                },
            ],
        }
    )

    assert all(item["kind"] != "pattern_instance_count_drop_offline_change" for item in candidates)


def test_pattern_candidates_extract_business_contract_data_quality() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "log_error_list",
                    "summary": (
                        "root_hints={'BadRequestException': 3, '不存在': 3, '参数非法': 2} "
                        "exceptions={'BadRequestException': 3}"
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_data_quality"


def test_pattern_candidates_extract_collation_mismatch_from_app_log_signal_summary() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "自定义监控"},
            "evidence": [
                {
                    "name": "sls_app_application_log_sentinel_OR_block",
                    "summary": (
                        'top_signals=["kind=app_sql_error '
                        'label=data_quality:collation_mismatch count=12"]'
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_data_quality"
    assert candidates[0]["label"] == "data_quality:collation_mismatch"


def test_tair_case_suppresses_app_side_data_quality_symptom() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "Tair"},
            "evidence": [
                {
                    "name": "log_error_list",
                    "summary": (
                        "root_hints={'BadRequestException': 3, '不存在': 3, '资格': 2} "
                        "exceptions={'com.wdk.infrastructure.foundation.exception.BadRequestException': 3}"
                    ),
                },
                {
                    "name": "trace_list_client_app_exact",
                    "summary": (
                        "trace spans=5 top=service=GET:47a4672918724d7f:tair.ldb.wdk:108 "
                        "result=-3989"
                    ),
                },
            ],
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "(tair@47a4672918724d7f:tair.ldb.wdk)",
                    "reason": "abnormal Tair GET span near alert window",
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_data_quality" for item in candidates)


def test_pattern_candidates_ignore_search_terms_when_sls_result_is_empty() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "sls_app_demo_sentinel_OR_block",
                    "command": 'sf log sls query --query "sentinel OR block"',
                    "summary": "app_logs count=0 top=",
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_limit" for item in candidates)


def test_pattern_candidates_extract_threadpool_busy_from_log_summary() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "sls_app_service_THREADPOOL_BUSY",
                    "summary": "kind=hsf_threadpool_busy label=THREADPOOL_BUSY:33.1.2.3 count=12",
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_threadpool_busy"
    assert candidates[0]["label"] == "33.1.2.3"


def test_pattern_candidates_extract_jvm_memory_pressure() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "JVM"},
            "evidence": [
                {
                    "name": "metric_jvm_memory_pool_used",
                    "summary": "metric=jvm_memory_pool_used ip=33.8.153.243 Metaspace rising Full GC",
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_jvm_memory"
    assert candidates[0]["label"] == "33.8.153.243"


def test_pattern_candidates_extract_external_connection_failure() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "异常日志"},
            "evidence": [
                {
                    "name": "log_error_list",
                    "summary": "dataservice-api.dw.alibaba-inc.com returned Connection reset repeatedly",
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_external_dependency"
    assert candidates[0]["label"] == "dataservice-api.dw.alibaba-inc.com"


def test_pattern_candidates_filter_java_net_when_extracting_external_domain() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "异常日志"},
            "evidence": [
                {
                    "name": "log_error_list",
                    "summary": (
                        "exceptions={'java.net.SocketException': 18} "
                        "domains={'java.net': 26, 'dataservice-api.dw.alibaba-inc.com': 4} "
                        "root_hints={'Connection reset': 18}"
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_external_dependency"
    assert candidates[0]["label"] == "dataservice-api.dw.alibaba-inc.com"


def test_pattern_candidates_extract_igraph_search_dependency() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "log_error_list",
                    "summary": (
                        "log_errors count=10 root_hints={'IGraphServerException': 15, "
                        "'igraph search error': 15} exceptions={"
                        "'com.taobao.igraph.client.common.IGraphServerException': 15}"
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_search_dependency"
    assert candidates[0]["label"] == "igraph"


def test_pattern_candidates_ignore_ordinary_igraph_trace_span() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "hippo-ha3search:SEARCH_asi_zjk_hippo_na61_01_virtual",
                    "reason": "normal child span service=igraph@PG result=00",
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_search_dependency" for item in candidates)


def test_pattern_candidates_extract_db_connection_pool_as_database_root() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "TDDL"},
            "evidence": [
                {
                    "name": "sls_app_tmi2_Druid",
                    "summary": "DruidDataSource get connection timeout from TDDL_CONN on host 33.70.176.208",
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_connection_pool"
    assert candidates[0]["label"] == "33.70.176.208"
    assert (
        infer_root_layer(candidates[0]["kind"], candidates[0]["label"], {}, candidates[0]["reason"])
        == "database"
    )


def test_pattern_candidates_infer_single_host_tddl_sql_failure_as_connection_pool() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "TDDL"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": (
                        "alarm app=tmi2 title=tmi2-SQL执行失败 content=采样: "
                        "[33.70.176.208#211b2b0f17833047026151774d0e5e]"
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_connection_pool"
    assert candidates[0]["label"] == "33.70.176.208"


def test_pattern_candidates_do_not_infer_connection_pool_from_tddl_success_rate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "TDDL"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarmTemplate_tddl写成功率 content=[33.61.93.210] tddl写成功率小于95%",
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_connection_pool" for item in candidates)


def test_pattern_candidates_extract_mq_spike_from_metric_topic() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "CPU"},
            "evidence": [
                {
                    "name": "metric_middleware_metaq_clnt_receive_group_id_qps",
                    "summary": (
                        "metric=middleware_metaq_clnt_receive_group_id_qps "
                        "topic=ae_gbrain_item_real_time_rebuild group_id=demo max=10000 trend=rising"
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_mq_spike"
    assert candidates[0]["label"] == "ae_gbrain_item_real_time_rebuild"
    assert candidates[0]["props"]["metric_signal"] is True


def test_pattern_candidates_extract_jvm_gc_pressure_without_full_gc_claim() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "自定义监控"},
            "evidence": [
                {
                    "name": "metric_jvm_gc_time_delta",
                    "summary": (
                        "metric=jvm_gc_time_delta series_count=69 top="
                        "[ip=33.42.120.77,app_group=aidc-finance-order_ae_betahost,"
                        "gc=g1_young_generation max=481,avg=81.18,last=51,trend=rising]"
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_jvm_gc_pressure"
    assert candidates[0]["label"] == "33.42.120.77"
    assert candidates[0]["props"]["gc_pressure"] is True
    assert (
        infer_root_layer(candidates[0]["kind"], candidates[0]["label"], {}, candidates[0]["reason"])
        == "infrastructure"
    )


def test_pattern_candidates_ignore_zero_only_full_gc_metric() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "JVM"},
            "evidence": [
                {
                    "name": "metric_jvm_gc_fgc_time",
                    "summary": (
                        "metric=jvm_gc_fgc_time series_count=23 top="
                        "[ip=33.42.120.77 min=0,max=0,avg=0,last=0,trend=stable]"
                    ),
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_jvm_memory" for item in candidates)


def test_pattern_candidates_skip_gc_pressure_for_tair_cases() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "Tair"},
            "evidence": [
                {
                    "name": "metric_jvm_gc_time_delta",
                    "summary": (
                        "metric=jvm_gc_time_delta series_count=69 top="
                        "[ip=11.248.89.141 gc=g1_young_generation max=857,trend=rising]"
                    ),
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_jvm_gc_pressure" for item in candidates)


def test_notify_business_failure_pattern_extracts_topic_and_app() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "METAQ"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=wdk-crowd-center metric=middleware_notify_receive_success_rate notify消费成功率 60%",
                },
                {
                    "name": "trace_list_server_app_exact",
                    "summary": (
                        "trace spans=8 top=server=wdk-crowd-center:wdk-crowd-centerhost "
                        "service=Notify@recv~BytesMessage:TC_REFUND_DISPUTE:RP-REFUND-AGRT-APPLIED:"
                        "P-RP3-DEFAULT-GID result=1"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_notify_business_failure"
    assert top["label"] == "wdk-crowd-center TC_REFUND_DISPUTE business_consume_failure"
    assert top["props"]["consume_failure"] is True


def test_config_mq_failure_pattern_extracts_diamond_rollout_root() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "OTHER"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": (
                        "alarm app=lazada-credit-core-s "
                        "metric=middleware_metaq_receive_success_rate metaq消费成功率异常"
                    ),
                },
                {
                    "name": "event_changefree_query",
                    "summary": (
                        "events count=1 top=change_system=aone change_type=CONFIG_PUSH "
                        "change_summary=Aone配置推送-类型:diamond "
                        "change_app=lazada-credit-core-s deploy_id=157967482 "
                        "dataId=result.notice.config group=AIPAY_PH002_lazada.credit.core "
                        "crIds=35281950"
                    ),
                },
                {
                    "name": "trace_list_server_app_exact",
                    "summary": (
                        "trace error_top=service=MQRecv@GL_CREDIT-INNER-NOTIFY-TOPIC_AIPAY_PH002:"
                        "CID_GL_CREDIT_INNER_NOTIFY_LISTENER_AIPAY_PH002:LOAN_DISCOUNT "
                        "result=1/BIZ_ERROR"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_config_mq_failure"
    assert top["label"] == (
        "lazada-credit-core-s result.notice.config CR=35281950 "
        "LOAN_DISCOUNT config_mq_business_failure"
    )
    assert top["props"]["config_change"] is True
    assert infer_root_layer(top["kind"], top["label"], top["props"], top["reason"]) == "change"


def test_config_mq_failure_pattern_preserves_external_org_context() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "OTHER"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": (
                        "alarm app=lazada-credit-core-s "
                        "metric=middleware_metaq_receive_success_rate metaq消费成功率异常"
                    ),
                },
                {
                    "name": "event_changefree_query",
                    "summary": (
                        "events count=1 change_system=aone change_type=CONFIG_PUSH "
                        "change_app=lazada-credit-core-s dataId=result.notice.config "
                        "group=AIPAY_PH002_lazada.credit.core crIds=35281950"
                    ),
                },
                {
                    "name": "sls_app_credit_core_prod",
                    "summary": (
                        "kind=metaq_business_failure label=GL_CREDIT-INNER-NOTIFY-TOPIC_AIPAY_PH002:"
                        "business_consume_failure:LOAN_DISCOUNT:lender=pera "
                        "business_tags=['LOAN_DISCOUNT'] external_orgs=['pera'] "
                        "api_names=['inner.lazcredit.paylater.inhouse.loan.discount.notify']"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_config_mq_failure"
    assert top["label"] == (
        "lazada-credit-core-s result.notice.config CR=35281950 "
        "LOAN_DISCOUNT lender=pera config_mq_business_failure"
    )
    assert top["props"]["external_org"] == "pera"
    assert top["props"]["api_name"] == "inner.lazcredit.paylater.inhouse.loan.discount.notify"


def test_mdm_master_data_pattern_extracts_table_from_trace_summary() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": (
                        "alarm app=aidc-finance-rebate-billing "
                        "content=ASCPBusinessPartnerFacade sync 成功率 当前值为: 0, 失败数 当前值为: 1"
                    ),
                },
                {
                    "name": "trace_get",
                    "summary": (
                        "error_top=client=(metaq@topic_ascp_vendor_info_change) "
                        "server=aidc-finance-rebate-billing:aidc-finance-rebate-billing_default_host "
                        "service=MQRecv@topic_ascp_vendor_info_change:CID:UPDATE result=01/ERR/BIZ_ERROR "
                        "sql_tables={'mdm_bank': 4, 'vendor_finance': 4} "
                        "sql_top=client=lzd-cfo-mdm:lzd-cfo-mdm__host server=(db@lzd_cfo_mdm) "
                        "service=TDDL_QUERY@lzd_cfo_mdm:mdm_bank\x1a8c6ee4f7"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_mdm_master_data_missing"
    assert top["label"] == "lzd-cfo-mdm mdm_bank ASCPBusinessPartnerFacade.sync master_data_missing"
    assert top["props"]["master_data_missing"] is True


def test_mdm_master_data_pattern_keeps_low_frequency_table_from_large_context() -> None:
    noisy_tables = {f"vendor_side_table_{index}": 50 - index for index in range(30)}
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": (
                        "alarm app=aidc-finance-rebate-billing "
                        "content=ASCPBusinessPartnerFacade sync 成功率 当前值为: 0, 失败数 当前值为: 1"
                    ),
                },
                {
                    "name": "trace_get",
                    "summary": (
                        f"trace spans=4000 sql_tables={noisy_tables} "
                        "error_top=client=(metaq@topic_ascp_vendor_info_change) "
                        "server=aidc-finance-rebate-billing:aidc-finance-rebate-billing_default_host "
                        "service=MQRecv@topic_ascp_vendor_info_change:CID:UPDATE result=01/ERR/BIZ_ERROR "
                        "sql_top=client=lzd-cfo-mdm:lzd-cfo-mdm__host server=(db@lzd_cfo_mdm) "
                        "service=TDDL_QUERY@lzd_cfo_mdm:mdm_bank\x1a8c6ee4f7"
                    ),
                },
            ],
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "(db@lzd_cfo_mdm)",
                    "reason": "abnormal trace span",
                    "props": {
                        "service": "TDDL_QUERY@lzd_cfo_mdm:mdm_bank\x1a8c6ee4f7",
                        "result_code": "00",
                    },
                }
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_mdm_master_data_missing"
    assert "mdm_bank" in top["label"]


def test_tddl_read_traffic_source_pattern_combines_upstream_service_and_table() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "TDDL"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=wdk-suppliercore metric=middleware_tddl_read_qps tddl读qps 当前值为 804",
                },
                {
                    "name": "metric_middleware_tddl_read_qps",
                    "summary": (
                        "metric=middleware_tddl_read_qps top=[app_group=wdk-suppliercorehost "
                        "max=830,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "summary": (
                        "trace spans=4000 sql_tables={'wdk_merchant_store_sku': 937, 'wdk_supplier': 409} "
                        "top=client=wdk-item-controller:wdk-item-controllerhost "
                        "server=wdk-suppliercore:wdk-suppliercorehost "
                        "service=com.wdk.suppliercore.client.service.WdkSupplierQueryService@getSupplierByCode~SS "
                        "duration_ms=4 result=01/ERR"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_tddl_read_traffic_source"
    assert top["label"] == (
        "wdk-item-controller -> wdk-suppliercore "
        "WdkSupplierQueryService.getSupplierByCode wdk_supplier read_qps_traffic_source"
    )
    assert top["props"]["traffic_source"] is True


def test_tddl_read_traffic_source_ignores_non_sql_table_dict_fields() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "TDDL"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=wdk-suppliercore metric=middleware_tddl_read_qps tddl读qps 当前值为 804",
                },
                {
                    "name": "trace_get",
                    "summary": (
                        "props={'code': 999, 'success': 1} "
                        "trace spans=4000 sql_tables={'wdk_supplier': 2} "
                        "top=client=wdk-item-controller:wdk-item-controllerhost "
                        "server=wdk-suppliercore:wdk-suppliercorehost "
                        "service=com.wdk.suppliercore.client.service.WdkSupplierQueryService@getSupplierByCode~SS "
                        "duration_ms=4 result=01/ERR"
                    ),
                },
            ],
        }
    )

    assert "wdk_supplier" in candidates[0]["label"]
    assert " code " not in candidates[0]["label"]


def test_pattern_candidates_ignore_empty_mq_metric_summary() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "CPU"},
            "evidence": [
                {
                    "name": "metric_middleware_metaq_clnt_receive_group_id_qps",
                    "summary": "metric=middleware_metaq_clnt_receive_group_id_qps series_count=0 top=",
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_mq_spike" for item in candidates)


def test_pattern_candidates_ignore_stable_mq_metric_summary() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "CPU"},
            "evidence": [
                {
                    "name": "metric_middleware_metaq_clnt_send_group_id_qps",
                    "summary": (
                        "metric=middleware_metaq_clnt_send_group_id_qps series_count=1 "
                        "top=[group_id=demo,topic=stable_send_topic max=35.5 avg=33.2 "
                        "trend=stable]"
                    ),
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_mq_spike" for item in candidates)


def test_pattern_candidates_use_primary_mq_metric_series_for_spike_detection() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "CPU"},
            "evidence": [
                {
                    "name": "metric_middleware_metaq_clnt_send_group_id_qps",
                    "summary": (
                        "metric=middleware_metaq_clnt_send_group_id_qps series_count=3 "
                        "top=[group_id=demo,topic=stable_high_topic max=57 avg=30 trend=stable]; "
                        "[group_id=demo,topic=tiny_rising_topic max=0.98 avg=0.17 trend=rising]"
                    ),
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_mq_spike" for item in candidates)


def test_empty_alarm_does_not_create_pattern_candidate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "input": "请诊断 alarmId=abc"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": '{"alarm_id": "", "app": "", "title": "", "content": ""}',
                }
            ],
            "root_candidates": [],
        }
    )

    assert candidates == []


def test_cache_pattern_requires_cache_and_timeout_in_same_observation() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"case_id": "case-1", "type": "HSF"},
            "evidence": [
                {
                    "name": "app_resources",
                    "summary": "resources include Redis r-8vb219d10038c044",
                },
                {
                    "name": "trace_get",
                    "summary": "provider-app returned HSF timeout on remote call",
                },
            ],
            "root_candidates": [],
        }
    )

    assert [item["kind"] for item in candidates] == []


def test_pattern_kind_controls_root_layer_before_reason_keywords() -> None:
    layer = infer_root_layer(
        "pattern_mq_spike",
        "MANHATTAN_TOPIC_FOR_AFA",
        {"pattern": "mq_spike"},
        "MetaQ 消费突增，同时旁路日志出现 TDDL_QUERY 慢查询字样",
    )

    assert layer == "message_queue"


def test_host_pattern_ignores_generic_trace_host_fields() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "trace_list",
                    "summary": (
                        "client_name=foo:foohost client_ip=11.1.1.1 "
                        "host_name=bar:barhost host_ip=33.1.1.1 resultType=3"
                    ),
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_host_anomaly" for item in candidates)


def test_host_pattern_extracts_abnormal_trace_target_ip() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "topology_trace_path",
                    "summary": (
                        "trace t1 topology path: consumer:host -> middle:middle_host "
                        "com.demo.MiddleApi@query~P 10002ms rc=01 server_ip=33.1.1.1 | "
                        "middle:middle_host -> provider:provider_doomhost "
                        "com.demo.ProviderApi@query~P 10002ms rc=03 server_ip=33.42.114.145"
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_host_anomaly"
    assert candidates[0]["label"] == "provider:provider_doomhost@33.42.114.145"
    assert candidates[0]["score"] >= 7.5
    assert "timeout" in candidates[0]["reason"]


def test_infra_event_pattern_extracts_ecs_memory_fault() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "OTHER"},
            "evidence": [
                {
                    "name": "event_query_app",
                    "summary": (
                        "events count=1 top=sourceProduct=ECS level=critical "
                        "instanceId=i-8vbiyp6wvmcp36j72a5u "
                        "type=acs.ecs[ecs:CloudMonitor:Instance[SystemMaintenance.Redeploy:Avoided]] "
                        "alertRuleName=local_disk_nc_down_hardware_error status=Avoided "
                        "reason=The host machine has potential failure risks;Memory error"
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_infra_event"
    assert candidates[0]["label"] == "i-8vbiyp6wvmcp36j72a5u hardware_memory_fault"
    assert (
        infer_root_layer(
            candidates[0]["kind"],
            candidates[0]["label"],
            candidates[0]["props"],
            candidates[0]["reason"],
        )
        == "infrastructure"
    )


def test_hsf_downstream_timeout_pattern_extracts_target_service() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "topology_trace_path",
                    "summary": (
                        "trace t1 topology path: alsc-saas-crm-groupon:"
                        "alsc-saas-crm-groupon_default_host -> "
                        "alsc-saas-thirdgw:alsc-saas-thirdgwhost "
                        "com.alsc.saas.thirdgw.client.biz.ThirdGwService@invoke~T "
                        "10190ms rc=03 server_ip=33.103.98.250"
                    ),
                }
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_hsf_downstream_timeout"
    assert (
        top["label"] == "alsc-saas-thirdgw ThirdGwService.invoke downstream_timeout@33.103.98.250"
    )
    assert top["props"]["threadpool_busy"] is False
    assert (
        infer_root_layer(top["kind"], top["label"], top["props"], top["reason"])
        == "service_dependency"
    )
    assert "without claiming provider-pool saturation" in top["reason"]


def test_hsf_downstream_timeout_prefers_downstream_from_alarm_app() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=fin-fund-solution title=ERROR关键字监控",
                },
                {
                    "name": "topology_trace_path",
                    "summary": (
                        "trace t1 topology path: fin-fund:fin-fundhost -> "
                        "fin-fund-solution:fin-fund-solutionhost "
                        "com.alibaba.fin.FundSolutionProxyFacade@collect~C "
                        "3849ms rc=03 server_ip=33.39.200.234 | "
                        "fin-fund-solution:fin-fund-solutionhost -> "
                        "fin-cif:fin-cif_hz_host "
                        "com.alibaba.b2b.fin.profile.api.facade.CifBankAccountFacade@query~B "
                        "3788ms rc=03 server_ip=33.62.98.154"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_hsf_downstream_timeout"
    assert top["label"] == (
        "fin-cif fin-cif_hz_host CifBankAccountFacade.query downstream_timeout@33.62.98.154"
    )


def test_hsf_downstream_timeout_keeps_single_word_downstream_app() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=alsc-dark-insight title=executeJob成功率应急告警",
                },
                {
                    "name": "topology_trace_path",
                    "summary": (
                        "trace t1 topology path: glaucus:glaucus_hippohost -> "
                        "alsc-dark-insight:alsc-dark-insighthost "
                        "com.alibaba.alsc.dark.insight.hsf.api.JobService@executeJob~J "
                        "58149ms rc=3 server_ip=33.39.246.59 | "
                        "alsc-dark-insight:alsc-dark-insighthost -> "
                        "kbtdatacenter:kbtdatacenterhost "
                        "com.alibaba.ktbdatacenter.protein.api.ProteinAutoFacade@standardListQuery~PP "
                        "114053ms rc=3 server_ip=33.61.186.43"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_hsf_downstream_timeout"
    assert (
        top["label"]
        == "kbtdatacenter ProteinAutoFacade.standardListQuery downstream_timeout@33.61.186.43"
    )


def test_hsf_threadpool_timeout_requires_direct_threadpool_signal() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "topology_trace_path",
                    "summary": (
                        "trace t1 topology path: consumer-app:consumer-apphost -> "
                        "provider-app:provider-apphost com.alibaba.demo.ProviderApi@getThing~P "
                        "3100ms rc=03 server_ip=33.1.2.3"
                    ),
                },
                {
                    "name": "sls_app_provider_THREADPOOL_BUSY",
                    "summary": "app_logs count=20 top_signals=[kind=hsf_threadpool_busy]",
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_hsf_threadpool_timeout"
    assert top["label"] == "provider-app ProviderApi.getThing threadpool_busy@33.1.2.3"
    assert top["props"]["threadpool_busy"] is True


def test_hsf_short_trace_error_extracts_default_group_timeout() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "trace_get_213e07971785",
                    "summary": (
                        "trace spans=871 hsf_error_top=client=hotel-buy:hotel-buyhost "
                        "server=tuan-item:tuan-item_default_production "
                        "service=com.alibaba.fliggy.tuan.item.api.hotel.UpRoomQueryApi@getUpRoomInfo~U "
                        "failures=2 max_duration_ms=748 result_codes={'03/TIMEOUT': 2} "
                        "provider_ips={'33.5.100.72': 2} consumer_ips={'33.3.251.222': 1}"
                    ),
                }
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_hsf_downstream_timeout"
    assert top["label"] == (
        "tuan-item tuan-item_default_production UpRoomQueryApi.getUpRoomInfo "
        "downstream_timeout@33.5.100.72"
    )
    assert top["props"]["failure_count"] == 2


def test_tddl_repeated_query_fanout_extracts_sql_root_from_hsf_timeout() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "trace_get_213e004d1784",
                    "summary": (
                        "trace spans=176 hsf_error_top=client=alsc-crm-discount:"
                        "alsc-crm-discount_default_host server=alsc-member-center:"
                        "alsc-member-centerhost service=com.alibaba.alscmembercenter."
                        "backend.facade.v2.api.card.CardQueryApi@listCardsByCustomer~L "
                        "failures=2 max_duration_ms=15252 result_codes={'03/TIMEOUT': 2} "
                        "provider_ips={'33.103.90.125': 1} consumer_ips={'33.70.164.147': 1} "
                        "sql_tables={'saas_card_template_relation': 30} "
                        "sql_top=client=alsc-member-center:alsc-member-centerhost "
                        "server=(db@alscmembercenter) service=TDDL_QUERY@alscmembercenter:"
                        "saas_card_template_relation\x1a8d8903c2 duration_ms=578 result=00/OK"
                    ),
                }
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_tddl_repeated_query_fanout"
    assert top["label"] == (
        "saas_card_template_relation repeated_sql_fanout alsc-member-center "
        "CardQueryApi.listCardsByCustomer"
    )
    assert top["props"]["repeat_count"] >= 30


def test_hsf_provider_subset_rpc_error_stays_soft_without_log_mechanism() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "自定义监控"},
            "evidence": [
                {
                    "name": "trace_get_2140e7b71784",
                    "summary": (
                        "trace spans=1371 hsf_error_top=client=lazada-ads-materiel:"
                        "lazada-ads-materiel_sg server=global-product-lazada-s:"
                        "global-product-lazada-s-seller-os30-live service=com.alibaba."
                        "global.ic.api.MerchantProductServiceFacade@queryProduct~P "
                        "failures=14 max_duration_ms=61 result_codes={'02/RPC_ERR/RPC_ERROR': 14} "
                        "provider_ips={'33.64.207.109': 6, '33.1.13.119': 5, '33.64.219.47': 3} "
                        "consumer_ips={'33.46.62.85': 4, '33.64.238.121': 4}"
                    ),
                }
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_hsf_provider_subset_rpc_error"
    assert top["label"] == (
        "global-product-lazada-s global-product-lazada-s-seller-os30-live "
        "MerchantProductServiceFacade.queryProduct provider_subset_rpc_error"
    )
    assert top["props"]["soft_mechanism"] is True


def test_hsf_provider_error_qps_spike_extracts_soft_provider_mechanism() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=mx-project metric=middleware_hsf_provider_success_rate hsf提供者成功率 54%",
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps series_count=1 "
                        "top=[app_group=mx-projecthost,"
                        "service=cn.damai.maitix.project.client.service.ProjectCenterService:1.0.0,"
                        "method=getProjectStructuredInfo~S min=0,max=339.05,avg=7.4137,last=0,trend=rising]"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_hsf_provider_error_qps_spike"
    assert top["label"] == (
        "mx-projecthost ProjectCenterService.getProjectStructuredInfo provider_error_qps_spike"
    )
    assert top["props"]["soft_mechanism"] is True
    assert (
        infer_root_layer(top["kind"], top["label"], top["props"], top["reason"])
        == "service_dependency"
    )
    assert all(item["kind"] != "pattern_limit" for item in candidates)


def test_hsf_provider_error_qps_spike_requires_provider_success_rate_context() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps series_count=1 "
                        "top=[app_group=mx-projecthost,"
                        "service=cn.damai.maitix.project.client.service.ProjectCenterService:1.0.0,"
                        "method=getProjectStructuredInfo~S min=0,max=339.05,avg=7.4137,last=0,trend=rising]"
                    ),
                },
            ],
        }
    )

    assert not any(item["kind"] == "pattern_hsf_provider_error_qps_spike" for item in candidates)


def test_hsf_provider_error_qps_spike_reads_json_metric_summary_text() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "hsf提供者成功率 [当前值为:54.385%]",
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "summary": (
                        '{"series_count": 100, "series": [{"labels": {'
                        '"app_group": "mx-projecthost", '
                        '"method": "getProjectStructuredInfo~S", '
                        '"service": "cn.damai.maitix.project.client.service.ProjectCenterService:1.0.0"'
                        '}, "summary": {"min": 0.0, "max": 339.05, '
                        '"avg": 7.4137, "last": 0.0, "trend": "rising"}}]}'
                    ),
                },
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_hsf_provider_error_qps_spike"
    assert "ProjectCenterService.getProjectStructuredInfo" in candidates[0]["label"]


def test_hsf_cold_start_capacity_pattern_extracts_none_core_group() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "trace_get",
                    "summary": (
                        "trace top=client=hexp:hexphost server=hotel-user-feature:"
                        "hotel-user-feature_none_core_host service="
                        "com.alibaba.trip.hoteluserfeature.client.api.FeatureWriteFacade"
                        "@refreshFeatureCache~U duration_ms=12000 result=03/TIMEOUT"
                    ),
                },
                {"name": "event_change_list", "summary": "changes=2 top=id=100; id=101"},
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_hsf_cold_start_capacity"
    assert "hotel-user-feature_none_core_host" in top["label"]
    assert "cold_start_high_load" in top["label"]
    assert infer_root_layer(top["kind"], top["label"], top["props"], top["reason"]) == "change"


def test_hsf_cold_start_capacity_pattern_extracts_grayhost_rt_groups() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=2 "
                        "top=[app_group=amap-s-hdriver_na620_host,"
                        "remote_app_group=amap-hitch-driver-pool_na620_grayhost,"
                        "remote_app_name=amap-hitch-driver-pool,"
                        "service=com.amap.aos.hitch.data.driver.hsf.DriverAutoGrabSettingService:1.0.0,"
                        "method=syncAutoGrabStatus~D min=3.75,max=12,avg=5.4,last=5.25,trend=rising]; "
                        "[app_group=amap-s-hdriver_na610_host,"
                        "remote_app_group=amap-hitch-driver-pool_na610_grayhost,"
                        "remote_app_name=amap-hitch-driver-pool,"
                        "service=com.amap.aos.hitch.data.driver.hsf.DriverAutoGrabSettingService:1.0.0,"
                        "method=syncAutoGrabStatus~D min=2,max=10.16,avg=2.73,last=2.25,trend=rising]"
                    ),
                }
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_hsf_cold_start_capacity"
    assert "amap-hitch-driver-pool_na620_grayhost" in top["label"]
    assert "amap-hitch-driver-pool_na610_grayhost" in top["label"]
    assert "groups=na620,na610" in top["label"]
    assert top["props"]["grayhost"] is True
    assert infer_root_layer(top["kind"], top["label"], top["props"], top["reason"]) == "change"


def test_hsf_grayhost_cold_start_requires_hsf_rising_rt() -> None:
    non_hsf = pattern_root_candidates(
        {
            "case": {"type": "TAIR"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=1 "
                        "top=[remote_app_group=app_na610_grayhost,trend=rising,max=12]"
                    ),
                }
            ],
        }
    )
    stable_hsf = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=1 "
                        "top=[remote_app_group=app_na610_grayhost,trend=stable,max=12]"
                    ),
                }
            ],
        }
    )

    assert not any(item["kind"] == "pattern_hsf_cold_start_capacity" for item in non_hsf)
    assert not any(item["kind"] == "pattern_hsf_cold_start_capacity" for item in stable_hsf)


def test_hsf_grayhost_cold_start_requires_regional_grayhost_group() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=1 "
                        "top=[app_group=caller_gray1_host,"
                        "remote_app_group=ele-waimai-marketing-ai_elezb_gray1_grayhost,"
                        "remote_app_name=ele-waimai-marketing-ai,"
                        "service=com.alibaba.alsc.dark.insight.hsf.api.JobService:1.0.0,"
                        "method=executeJob~J min=1,max=12,avg=5,last=8,trend=rising]"
                    ),
                }
            ],
        }
    )

    assert not any(item["kind"] == "pattern_hsf_cold_start_capacity" for item in candidates)


def test_hsf_grayhost_cold_start_accepts_json_metric_summary() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "summary": (
                        '{"series_count": 1, "series": [{"labels": {'
                        '"app_group": "amap-s-hdriver_na620_host", '
                        '"remote_app_group": "amap-hitch-driver-pool_na620_grayhost", '
                        '"remote_app_name": "amap-hitch-driver-pool", '
                        '"service": "com.amap.aos.hitch.data.driver.hsf.'
                        'DriverAutoGrabSettingService:1.0.0", '
                        '"method": "syncAutoGrabStatus~D"}, '
                        '"summary": {"min": 3.75, "max": 12.0, '
                        '"avg": 5.4, "last": 5.25, "trend": "rising"}}]}'
                    ),
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_hsf_cold_start_capacity"
    assert "amap-hitch-driver-pool_na620_grayhost" in candidates[0]["label"]
    assert "DriverAutoGrabSettingService:1.0.0#syncAutoGrabStatus~D" in candidates[0]["label"]


def test_app_publish_data_quality_pattern_extracts_release_lifecycle_root() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "OTHER"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=mp-fund content=DP_CREATE NO_QUALIFICATION 定品未创建",
                },
                {
                    "name": "event_changefree_query",
                    "summary": (
                        "events count=1 top=sourceProduct=CHANGEFREE_EXE "
                        "change_system=normandy change_type=APP_PUBLISH "
                        "change_summary=应用mp-fund部署production环境 "
                        "deploy_id=157962710 deploy_version=234485125 batch=2/3"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_app_publish_data_quality"
    assert "mp-fund" in top["label"]
    assert "deploy_id=157962710" in top["label"]
    assert infer_root_layer(top["kind"], top["label"], top["props"], top["reason"]) == "change"


def test_downstream_offline_change_pattern_extracts_called_app_capacity_root() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "JVM"},
            "evidence": [
                {
                    "name": "event_changefree_query_freight_template",
                    "summary": (
                        "events count=2 top=sourceProduct=CHANGEFREE_EXE "
                        "change_system=normandy-director change_type=CONFIG_PUSH "
                        "change_summary=freight-template 应用变更 "
                        "change_app=freight-template detail_url=https://n.alibaba-inc.com/"
                        "micro/ops/app/freight-template/action/res/offline/detail "
                        "id=3033872029"
                    ),
                },
                {
                    "name": "log_error_list",
                    "summary": (
                        "HSFTimeOutException THREADPOOL_BUSY Provider's HSF thread pool is full "
                        "remote_app_name=freight-template"
                    ),
                },
            ],
        }
    )

    top = candidates[0]

    assert top["kind"] == "pattern_downstream_offline_change"
    assert "freight-template" in top["label"]
    assert "change_id=3033872029" in top["label"]
    assert infer_root_layer(top["kind"], top["label"], top["props"], top["reason"]) == "change"


def test_downstream_offline_change_pattern_ignores_plain_config_push() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "METAQ"},
            "evidence": [
                {
                    "name": "event_changefree_query",
                    "summary": (
                        "events count=1 top=sourceProduct=CHANGEFREE_EXE "
                        "change_system=aone change_type=CONFIG_PUSH change_result=变更成功 "
                        "change_summary=Aone配置推送-类型:diamond-环境:配置变更专用环境 "
                        "change_app=lazada-credit-core-s id=2993075084"
                    ),
                },
                {
                    "name": "trace_list",
                    "summary": "MQRecv result=1 BIZ_ERROR timeout app=lazada-credit-core-s",
                },
            ],
        }
    )

    assert not any(item["kind"] == "pattern_downstream_offline_change" for item in candidates)


def test_downstream_offline_change_pattern_requires_same_app_failure_context() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "event_changefree_query_aserver",
                    "summary": (
                        "events count=1 top=sourceProduct=CHANGEFREE_EXE "
                        "change_type=OFFLINE_HOST change_summary=aserver 应用变更 "
                        "change_app=aserver detail_url=https://n.alibaba-inc.com/"
                        "micro/ops/app/aserver/action/res/offline/detail id=2917684340"
                    ),
                },
                {
                    "name": "log_error_list",
                    "summary": (
                        "HSFTimeOutException THREADPOOL_BUSY Provider's HSF thread pool is full "
                        "remote_app_name=fin-cif"
                    ),
                },
            ],
        }
    )

    assert not any(item["kind"] == "pattern_downstream_offline_change" for item in candidates)


def test_host_pattern_ignores_tair_trace_target_ip() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "Tair"},
            "evidence": [
                {
                    "name": "topology_trace_path",
                    "summary": (
                        "trace t1 topology path: app:host -> cache:cache_host "
                        "GET:47a4672918724d7f:tair.ldb.wdk:108 501ms rc=03 "
                        "server_ip=33.42.114.145"
                    ),
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_host_anomaly" for item in candidates)


def test_host_pattern_requires_abnormal_trace_target_signal() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "HSF"},
            "evidence": [
                {
                    "name": "topology_trace_path",
                    "summary": (
                        "trace t1 topology path: consumer:host -> provider:provider_host "
                        "com.demo.ProviderApi@query~P 12ms rc=00 server_ip=33.42.114.145"
                    ),
                }
            ],
        }
    )

    assert all(item["kind"] != "pattern_host_anomaly" for item in candidates)


def test_tddl_duration_span_creates_sql_pattern_candidate() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "TDDL"},
            "evidence": [
                {
                    "name": "trace_get",
                    "summary": "TDDL_QUERY@intl_bw:resource_lock_setting_his duration=2646ms",
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_slow_sql"
    assert candidates[0]["label"] == "resource_lock_setting_his"


def test_retrieval_summary_does_not_precede_structured_sql_evidence() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "TDDL"},
            "retrieval_summary": "noisy summary mentions TDDL_QUERY@crm_aegean:global_customer duration=1ms",
            "evidence": [
                {
                    "name": "trace_get",
                    "summary": "TDDL_QUERY@intl_bw:resource_lock_setting_his duration=2646ms",
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_slow_sql"
    assert candidates[0]["label"] == "resource_lock_setting_his"


def test_cache_pattern_does_not_use_summary_hash_as_entity_label() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "自定义监控"},
            "retrieval_summary": "data_ref=01a0330f-2efc-7f72-bbd5-a3c1d5dc1d77",
            "evidence": [
                {
                    "name": "log_error_list",
                    "summary": "Redis Pipeline read timed out, data_ref ac7a-eed5f3d25c28",
                }
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_cache_timeout"
    assert candidates[0]["label"] == "cache_timeout"


def test_schedulerx_batch_job_can_explain_rds_cpu_load() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "TDDL"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": (
                        "alarm app=titanium-task metric=cms.acs_rds_dashboard.CpuUsage "
                        "content=RDS CPU rm-8vb0b439ftfyx370r current=99.83"
                    ),
                },
                {
                    "name": "event_query_app",
                    "summary": (
                        "events count=10 top=sourceProduct=SchedulerX level=info "
                        "subject=1025599413_78335451819 "
                        "type=com.alibaba.schedulerx.job.start status=success "
                        "privateIp=33.7.216.246"
                    ),
                },
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_schedulerx_batch_load"
    assert candidates[0]["label"] == "titanium-task SchedulerX job 1025599413 rds_cpu_load"
    assert candidates[0]["props"]["job_id"] == "1025599413"


def test_schedulerx_pattern_prefers_longer_start_end_job_pair() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "TDDL"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=titanium-task metric=cms.acs_rds_dashboard.CpuUsage RDS CPU",
                },
                {
                    "name": "event_query_app",
                    "summary": (
                        "events count=4 top=sourceProduct=SchedulerX subject=894308998_1 "
                        "type=com.alibaba.schedulerx.job.start time=1000; "
                        "sourceProduct=SchedulerX subject=894308998_1 "
                        "type=com.alibaba.schedulerx.job.end time=1001; "
                        "sourceProduct=SchedulerX subject=1025599413_1 "
                        "type=com.alibaba.schedulerx.job.start time=1000; "
                        "sourceProduct=SchedulerX subject=1025599413_1 "
                        "type=com.alibaba.schedulerx.job.end time=1030"
                    ),
                },
            ],
        }
    )

    assert candidates[0]["props"]["job_id"] == "1025599413"


def test_auth_session_failure_pattern_uses_trace_401_and_proxy_context() -> None:
    candidates = pattern_root_candidates(
        {
            "case": {"type": "自定义监控"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=goc-pass title=goc_pass_后端代理(nginx) content=[gocFaultDef] 失败数",
                },
                {
                    "name": "trace_list_server_app_exact",
                    "summary": (
                        "server=goc-pass:goc-passhost service=https://tr.alibaba-inc.com/"
                        "gocFaultDef/innerApi/v2/incident/scenarios/level/defs "
                        "duration_ms=180 result=401/UNAUTHORIZED/RPC_ERROR"
                    ),
                },
            ],
        }
    )

    assert candidates[0]["kind"] == "pattern_auth_session_failure"
    assert candidates[0]["label"] == "goc-pass gocFaultDef http_401 auth_session_failure"
    assert candidates[0]["props"]["status"] == "401"
