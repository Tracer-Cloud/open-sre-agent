from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.features import (
    entity_features,
    infer_modality,
    infer_root_layer,
    token_features,
)


def _graph_context() -> dict[str, object]:
    return {
        "case": {
            "case_id": "case-1",
            "split": "test",
            "type": "HSF",
            "data_ref": "snapshot-1",
        },
        "ontology": ["Case", "Alarm", "Service", "Trace", "MetricSeries"],
        "retrieval_summary": "prefer upstream provider root cause",
        "root_candidates": [
            {
                "kind": "trace_span",
                "label": "provider-app:provider_group",
                "score": 5.0,
                "reason": "abnormal trace span",
                "props": {
                    "trace_id": "212a6a3417840231458777961e0d45",
                    "client": "consumer-app:consumer_group",
                    "server": "provider-app:provider_group",
                    "service": "com.alibaba.demo.ProviderApi:1.0.0@getThing~P",
                    "result_code": "03",
                    "duration_ms": 10000,
                },
            }
        ],
        "evidence": [
            {
                "name": "alarm_get",
                "command": "sf alarm get abc -f json",
                "returncode": 0,
                "summary": "consumer HSF success rate dropped for provider-app",
                "raw_path": "/tmp/alarm.json",
            },
            {
                "name": "trace_get",
                "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                "returncode": 0,
                "summary": "provider-app com.alibaba.demo.ProviderApi@getThing timed out at 10000ms",
                "raw_path": "/tmp/trace.json",
            },
            {
                "name": "metric_middleware_hsf_provider_service_method_rt",
                "command": "sf metric query middleware_hsf_provider_service_method_rt -f json",
                "returncode": 0,
                "summary": "provider-app com.alibaba.demo.ProviderApi getThing service RT rose sharply during alarm window",
                "raw_path": "/tmp/metric.json",
            },
        ],
    }


def test_build_evidence_bundle_links_modalities_and_entities() -> None:
    bundle = build_evidence_bundle(_graph_context())

    assert bundle.case_id == "case-1"
    assert bundle.case_type == "HSF"
    assert bundle.hypotheses
    top = bundle.hypotheses[0]
    assert top.root_layer == "service_dependency"
    assert "trace" in top.modalities
    assert "metric" in top.modalities
    assert top.entities["services"] == ["com.alibaba.demo.providerapi:1.0.0"]
    support_modalities = {item.modality for item in top.support}
    assert {"metric", "trace"} <= support_modalities


def test_metric_modality_does_not_match_log_substring_in_app_name() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "command": (
                        "sf metric query sum by(remote_app_name)"
                        "(middleware_hsf_consumer_service_method_error_qps{"
                        'remote_app_name="logisticsmarket-center"}) -f json'
                    ),
                    "returncode": 0,
                    "summary": {"series_count": 0, "series": []},
                }
            ],
        }
    )

    assert bundle.evidence[0].modality == "metric"


def test_mq_metric_topic_outranks_context_topic_text() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "CPU"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "CPU alarm mentions topic=ae-brain-sku-gbrain-realtime-change-topic "
                        "and message qps spike as surrounding context"
                    ),
                },
                {
                    "name": "metric_middleware_metaq_clnt_receive_group_id_qps",
                    "command": "sf metric query middleware_metaq_clnt_receive_group_id_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_metaq_clnt_receive_group_id_qps "
                        "topic=ae_gbrain_item_real_time_rebuild group_id=gbrain max=3834 avg=2100 "
                        "trend=rising"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_mq_spike"
    assert top.label == "ae_gbrain_item_real_time_rebuild"
    assert top.root_layer == "message_queue"


def test_mq_metric_topic_outranks_app_group_receive_metric() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "CPU"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_metaq_receive_qps",
                    "command": "sf metric query middleware_metaq_receive_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_metaq_receive_qps series_count=24 "
                        "top=[app_group=ae-brain-data-d_sg_host,ip=11.128.56.146 "
                        "max=174.6 avg=89.48 trend=rising]"
                    ),
                },
                {
                    "name": "metric_middleware_metaq_clnt_receive_group_id_qps",
                    "command": "sf metric query middleware_metaq_clnt_receive_group_id_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_metaq_clnt_receive_group_id_qps series_count=3 "
                        "top=[group_id=CID-AE-BRAIN-DATA-GBRAIN-INDEX-REBUILD-CONSUMER,"
                        "topic=ae_gbrain_item_real_time_rebuild max=3834 avg=3187 trend=rising]"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_mq_spike"
    assert top.label == "ae_gbrain_item_real_time_rebuild"


def test_metaq_metric_spike_outranks_generic_producer_host_anomaly() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "METAQ"},
            "root_candidates": [
                {
                    "kind": "pattern_host_anomaly",
                    "label": "producer-app@33.2.239.180",
                    "score": 8.25,
                    "reason": "producer-app CPU/load high while MetaQ alarm is firing",
                }
            ],
            "evidence": [
                {
                    "name": "metric_middleware_metaq_clnt_receive_group_id_qps",
                    "command": "sf metric query middleware_metaq_clnt_receive_group_id_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_metaq_clnt_receive_group_id_qps series_count=3 "
                        "top=[group_id=CID-AE-BRAIN-DATA-GBRAIN-INDEX-REBUILD-CONSUMER,"
                        "topic=gbrain_item_rt_tag_update max=3834 avg=3187 trend=rising]"
                    ),
                }
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_mq_spike"
    assert top.label == "gbrain_item_rt_tag_update"


def test_security_scan_tddl_write_conflict_outranks_unrelated_trace_sql() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "TDDL"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "(db@demo_unit)",
                    "score": 4.0,
                    "reason": "abnormal trace span",
                    "props": {
                        "service": "TDDL_INSERT@demo_unit:robotx_chat_log\x1a35978e7c",
                        "result_code": "1",
                        "user_data": "heimdall=1 @s0=mtop.demo.ask",
                    },
                },
                {
                    "kind": "trace_span",
                    "label": "(db@read_db)",
                    "score": 4.0,
                    "reason": "normal side query",
                    "props": {
                        "service": "TDDL_QUERY@read_db:alime_robot",
                        "result_code": "00",
                    },
                },
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": "fliggy-robotx tddl写成功率 dropped on one host",
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": (
                        "http://h5api.m.taobao.com/h5/mtop.demo.ask/1.0/ heimdall=1 "
                        "TDDL_INSERT@demo_unit:robotx_chat_log\x1a35978e7c result=1 "
                        "TDDL_QUERY@read_db:alime_robot result=00"
                    ),
                },
                {
                    "name": "metric_middleware_tddl_write_success_rate",
                    "command": "sf metric query middleware_tddl_write_success_rate -f json",
                    "returncode": 0,
                    "summary": "metric=middleware_tddl_write_success_rate app_group=fliggy-robotx trend=falling",
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_security_sql_conflict"
    assert top.label == "robotx_chat_log unique_key_conflict"
    assert top.root_layer == "database"


def test_hsf_metric_labels_create_service_method_root_candidate() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_consumer_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": {
                        "series_count": 1,
                        "series": [
                            {
                                "labels": {
                                    "app_group": "mainring-offline_host",
                                    "remote_app_name": "tradeplatform3",
                                    "service": "com.taobao.trade.platform.api.query.SellerQueryService:1.0.0",
                                    "method": "queryCount~lQ",
                                },
                                "summary": {"max": 394.26, "trend": "rising"},
                            }
                        ],
                    },
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get 210841eb17826923209095753da24b -f json",
                    "returncode": 0,
                    "summary": "mainring calls tradeplatform3 SellerQueryService queryCount and gets TCException",
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "hsf_service_method"
    assert top.root_layer == "service_dependency"
    assert "tradeplatform3" in top.label
    assert "queryCount~lQ" in top.label
    assert {"metric", "trace"} <= set(top.modalities)


def test_hsf_metric_rising_provider_series_outranks_stable_zero_series() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_consumer_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_error_qps series_count=1 "
                        "top=[app_group=consumer_host,remote_app_name=provider,"
                        "service=com.demo.NoisyService:1.0.0,method=noisy~N "
                        "min=0,max=0,avg=0,last=0,trend=stable]"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_provider_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps series_count=1 "
                        "top=[app_group=provider_host,service=com.demo.TaxCalApplicationService:1.0.0,"
                        "method=taxCalForItem~I min=0,max=1932,avg=326,last=0,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "provider TaxCalApplicationService taxCalForItem timeout",
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "hsf_service_method"
    assert "TaxCalApplicationService" in bundle.hypotheses[0].label
    assert "taxCalForItem~I" in bundle.hypotheses[0].label


def test_hsf_provider_error_qps_breaks_tie_against_success_rate_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_provider_service_method_success_rate",
                    "command": "sf metric query middleware_hsf_provider_service_method_success_rate -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_success_rate series_count=1 "
                        "top=[app_group=tariffcodehost,service=com.demo.HscodePostalReadService:1.0.0,"
                        "method=batchQueryByHscodeList~L min=2,max=38,avg=13,last=13,trend=falling]"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_provider_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps series_count=1 "
                        "top=[app_group=tariffcodehost,service=com.demo.TaxCalApplicationService:1.0.0,"
                        "method=taxCalForItem~I min=0,max=1932,avg=326,last=0,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "tariffcode TaxCalApplicationService taxCalForItem request failed",
                },
            ],
        }
    )

    assert "TaxCalApplicationService" in bundle.hypotheses[0].label
    assert "taxCalForItem~I" in bundle.hypotheses[0].label
    assert "limit" in bundle.hypotheses[0].entities["keywords"]
    assert "接口限流" in bundle.hypotheses[0].reason


def test_hsf_provider_error_qps_needs_large_spike_for_limit_mechanism() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_provider_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps series_count=1 "
                        "top=[app_group=cngdchost,service=com.demo.DispatchExecuteService:1.0.0,"
                        "method=execute~S min=0,max=150,avg=25,last=0,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "cngdc DispatchExecuteService execute timeout",
                },
            ],
        }
    )

    assert "limit" not in bundle.hypotheses[0].entities["keywords"]
    assert "接口限流" not in bundle.hypotheses[0].reason


def test_sentinel_limit_pattern_outranks_hsf_service_metric_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_provider_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps series_count=1 "
                        "top=[app_group=service-host,service=com.demo.PriceQueryService:1.0.0,"
                        "method=batchQueryPrice~B min=0,max=22,avg=3,last=0,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": (
                        "service-host PriceQueryService batchQueryPrice fails with "
                        "SentinelBlockException blockexception 接口限流"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_limit"
    assert top.root_layer == "middleware_limit"
    assert "Sentinel" in top.reason or "限流" in top.reason


def test_hsf_provider_error_qps_soft_pattern_outranks_plain_metric() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get alarm -f json",
                    "returncode": 0,
                    "summary": "alarm app=mx-project metric=middleware_hsf_provider_success_rate hsf提供者成功率 54%",
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_provider_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps series_count=1 "
                        "top=[app_group=mx-projecthost,"
                        "service=cn.damai.maitix.project.client.service.ProjectCenterService:1.0.0,"
                        "method=getProjectStructuredInfo~S min=0,max=339.05,avg=7.4137,last=0,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "mx-project ProjectCenterService getProjectStructuredInfo request failed",
                },
            ],
        }
    )

    hypothesis = bundle.hypotheses[0]

    assert hypothesis.kind == "pattern_hsf_provider_error_qps_spike"
    assert "ProjectCenterService.getProjectStructuredInfo" in hypothesis.label
    assert "soft provider error mechanism" in hypothesis.reason


def test_hsf_offline_capacity_change_pattern_outranks_provider_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_provider_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps series_count=1 "
                        "top=[app_group=cngdchost,service=com.demo.DispatchExecuteService:1.0.0,"
                        "method=execute~S min=0,max=150,avg=25,last=0,trend=rising]"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "command": "sf metric query middleware_hsf_consumer_service_method_rt -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=1 "
                        "top=[app_group=cngdchost,service=com.cainiao.global.RouteLineService:1.0.0.offline,"
                        "method=routeLine~CC min=1073,max=208900,avg=94490,last=1073,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": (
                        "trace top=client=cngdc:cngdchost server=cnexport-cb-route:"
                        "cnexport-cb-route_offline_host service=com.cainiao.global.RouteLineService:"
                        "1.0.0.offline@routeLine~CC duration_ms=28075 result=03/TIMEOUT"
                    ),
                },
                {
                    "name": "event_change_list",
                    "command": "sf event change list --app cngdc --infra -f json",
                    "returncode": 0,
                    "summary": "changes=3 top=id=2909444204; id=2909530410",
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_capacity_change"
    assert top.root_layer == "change"
    assert "cnexport-cb-route" in top.label
    assert "RouteLineService:1.0.0.offline#routeLine~CC" in top.label
    assert {"event", "metric", "trace"} <= set(top.modalities)


def test_hsf_downstream_timeout_pattern_outranks_entry_metric_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": "alarm app=alsc-saas-crm-groupon title=success rate down",
                },
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_consumer_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_error_qps series_count=1 "
                        "top=[app_group=alsc-saas-crm-groupon_default_host,"
                        "service=com.alsc.saas.thirdgw.client.biz.ThirdGwService,"
                        "method=invoke~T min=0,max=0.2,avg=0.01,last=0,trend=rising]"
                    ),
                },
                {
                    "name": "topology_trace_path",
                    "returncode": 0,
                    "summary": (
                        "trace t1 topology path: alsc-saas-crm-groupon:"
                        "alsc-saas-crm-groupon_default_host -> "
                        "alsc-saas-thirdgw:alsc-saas-thirdgwhost "
                        "com.alsc.saas.thirdgw.client.biz.ThirdGwService@invoke~T "
                        "10190ms rc=03 server_ip=33.103.98.250"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_hsf_downstream_timeout"
    assert "alsc-saas-thirdgw" in top.label
    assert "ThirdGwService.invoke" in top.label
    assert {"metric", "trace"} <= set(top.modalities)


def test_hsf_cold_start_capacity_pattern_outranks_entry_metric_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_success_rate",
                    "command": "sf metric query middleware_hsf_consumer_service_method_success_rate -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_success_rate series_count=1 "
                        "top=[app_group=hexphost,remote_app_name=hotel-user-feature,"
                        "service=com.alibaba.trip.hoteluserfeature.client.api.FeatureWriteFacade,"
                        "method=refreshFeatureCache~U min=0,max=1,avg=0.8,last=1,trend=falling]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": (
                        "trace top=client=hexp:hexphost server=hotel-user-feature:"
                        "hotel-user-feature_none_core_host service="
                        "com.alibaba.trip.hoteluserfeature.client.api.FeatureWriteFacade"
                        "@refreshFeatureCache~U duration_ms=12000 result=03/TIMEOUT"
                    ),
                },
                {
                    "name": "event_change_list",
                    "command": "sf event change list --app hotel-user-feature --infra -f json",
                    "returncode": 0,
                    "summary": "changes=2 top=id=100; id=101",
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_hsf_cold_start_capacity"
    assert "hotel-user-feature_none_core_host" in top.label
    assert top.root_layer == "change"
    assert {"event", "metric", "trace"} <= set(top.modalities)


def test_hsf_grayhost_cold_start_pattern_outranks_entry_metric_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get alarm-1 -f json",
                    "returncode": 0,
                    "summary": (
                        "amap-s-hdriver_HSF消费者指标监控 - 单机生产者tp90耗时 "
                        "service=com.amap.aos.hitch.data.driver.hsf."
                        "DriverAutoGrabSettingService:1.0.0 method=syncAutoGrabStatus~D"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "command": "sf metric query middleware_hsf_consumer_service_method_rt -f json",
                    "returncode": 0,
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
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_hsf_cold_start_capacity"
    assert "amap-hitch-driver-pool_na620_grayhost" in top.label
    assert "amap-hitch-driver-pool_na610_grayhost" in top.label
    assert top.root_layer == "change"
    assert "metric" in top.modalities


def test_hsf_consumer_alarm_prioritizes_consumer_service_method() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "alarm app=fliggy-artificial-service "
                        "metric=middleware_hsf_consumer_success_rate title=hsf消费者成功率下跌"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_rt",
                    "command": "sf metric query middleware_hsf_provider_service_method_rt -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_rt series_count=1 "
                        "top=[app_group=fliggy-artificial-service_default_host,"
                        "service=com.demo.TransferArtificialService:1.0.0,"
                        "method=executeArtificialChains~A min=6081,max=8669,avg=7493,last=7236,trend=stable]"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "command": "sf metric query middleware_hsf_consumer_service_method_rt -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=1 "
                        "top=[app_group=fliggy-artificial-service_default_host,"
                        "remote_app_name=fliggy-alime-strategy,"
                        "service=com.alibaba.alime.strategy.api.service.flow.FlowService:1.0.0,"
                        "method=execute~E min=3203,max=4382,avg=3798,last=3530,trend=stable]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "fliggy-artificial-service calls fliggy-alime-strategy FlowService execute timeout",
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert "FlowService" in top.label
    assert "fliggy-alime-strategy" in top.label
    assert "超时" in top.reason


def test_non_hsf_case_does_not_boost_hsf_provider_error_qps_side_signal() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "Tair"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_provider_service_method_success_rate",
                    "command": "sf metric query middleware_hsf_provider_service_method_success_rate -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_success_rate series_count=1 "
                        "top=[app_group=mp-playstation_default_host,service=com.demo.AladdinLamp:1.0.0,"
                        "method=execute~R min=2,max=38,avg=13,last=13,trend=falling]"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_provider_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps series_count=1 "
                        "top=[app_group=mp-playstation_default_host,service=com.demo.SideService:1.0.0,"
                        "method=side~S min=0,max=1932,avg=326,last=0,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "mp-playstation AladdinLamp execute and SideService side both appear in trace",
                },
            ],
        }
    )

    scores = {item.label: item.score for item in bundle.hypotheses}
    side_score = scores["mp-playstation_default_host:com.demo.SideService:1.0.0#side~S"]
    aladdin_score = scores["mp-playstation_default_host:com.demo.AladdinLamp:1.0.0#execute~R"]

    assert side_score == aladdin_score


def test_hsf_metric_labels_survive_clipped_json_summary() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_consumer_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        '{"series_count": 100, "series": [{"labels": {"__name__": "", '
                        '"app_group": "mainring-offline_host", "method": "queryCount~lQ", '
                        '"service": "com.taobao.trade.platform.api.query.SellerQueryService:1.0.0"}, '
                        '"summary": {"max": 122.6}, "points": [{"time": "1782689640", "value": '
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get 210841eb17826923209095753da24b -f json",
                    "returncode": 0,
                    "summary": "mainring calls SellerQueryService queryCount and gets timeout",
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "hsf_service_method"
    assert "mainring-offline_host" in top.label
    assert "SellerQueryService" in top.label
    assert {"metric", "trace"} <= set(top.modalities)


def test_hsf_metric_compact_blocks_create_independent_candidates() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_consumer_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_error_qps series_count=100 "
                        "top=[app_group=g-hse1_corehost_spe,service=com.alibaba.trip.ump.service."
                        "ITripUmpSearchService:1.0.0,method=findPromotion4Search~U max=15.52,"
                        "trend=rising]; [app_group=g-hse1_core_host,service=com.taobao.trip."
                        "hsummary.client.bedtype.BedTypeSearchService:1.0.0,"
                        "method=searchBedInfoByRidList~L max=0.7,trend=falling]"
                    ),
                }
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "hsf_service_method"
    assert bundle.hypotheses[0].label == (
        "g-hse1_corehost_spe:com.alibaba.trip.ump.service."
        "ITripUmpSearchService:1.0.0#findPromotion4Search~U"
    )


def test_concrete_cache_timeout_pattern_can_outrank_hsf_metric_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "validation",
                "type": "自定义监控",
                "data_ref": "snap",
            },
            "evidence": [
                {
                    "name": "metric_middleware_hsf_provider_service_method_rt",
                    "command": "sf metric query middleware_hsf_provider_service_method_rt -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_rt series_count=100 "
                        "top=[app_group=aidc-finance-order_ae_host,service=com.alibaba.ascp."
                        "sales.order.erp.application.service.local.LocalService:1.0.0.aidc,"
                        "method=lazadaOrderProcess~SSS min=74,max=5000,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get 2101062a1784 -f json",
                    "returncode": 0,
                    "summary": (
                        "root path shows Redis instance r-t4n535b1b9c6a474 read timed out "
                        "before HSF provider rt increased"
                    ),
                },
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app demo -f json",
                    "returncode": 0,
                    "summary": "JedisConnectionException query timeout on r-t4n535b1b9c6a474",
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_cache_timeout"
    assert top.label == "r-t4n535b1b9c6a474"


def test_igraph_search_dependency_pattern_outranks_hsf_metric_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "command": "sf metric query middleware_hsf_consumer_service_method_rt -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=100 "
                        "top=[app_group=ae-sellingpoint-s_de46_host,service=com.alibaba."
                        "global.profile.api.api.general.MatchService:1.0.0,method=match2List~G "
                        "max=10974,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get 0b8848bf1781 -f json",
                    "returncode": 0,
                    "summary": (
                        "client=ae-sellingpoint-s server=global-profile-s "
                        "service=com.alibaba.global.profile.api.api.general.MatchService@match2List~G "
                        "duration_ms=10974 result=03/TIMEOUT"
                    ),
                },
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app ae-sellingpoint-s -f json",
                    "returncode": 0,
                    "summary": (
                        "log_errors count=10 root_hints={'IGraphServerException': 15, "
                        "'igraph search error': 15} exceptions={"
                        "'com.taobao.igraph.client.common.IGraphServerException': 15}"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_search_dependency"
    assert top.label == "igraph"
    assert top.root_layer == "service_dependency"


def test_support_keeps_trace_when_many_metrics_match_hsf_candidate() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_consumer_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": {
                        "series_count": 1,
                        "series": [
                            {
                                "labels": {
                                    "app_group": "mainring-offline_host",
                                    "service": "com.taobao.trade.platform.api.query.SellerQueryService:1.0.0",
                                    "method": "queryCount~lQ",
                                },
                                "summary": {"max": 394.26, "trend": "rising"},
                            }
                        ],
                    },
                },
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "command": "sf metric query middleware_hsf_consumer_service_method_rt -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_rt series_count=1 "
                        "top=[app_group=mainring-offline_host,service=other.Service,method=otherMethod max=99]"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_consumer_service_method_success_rate",
                    "command": "sf metric query middleware_hsf_consumer_service_method_success_rate -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_success_rate series_count=1 "
                        "top=[app_group=mainring-offline_host,service=another.Service,method=anotherMethod max=99]"
                    ),
                },
                {
                    "name": "trace_list_client_app_exact",
                    "command": "sf trace list --client-app mainring-offline_host -f json",
                    "returncode": 0,
                    "summary": (
                        "client_name=mainring:mainring-offline_host server_name=tradeplatform3:tp3g3host "
                        "service=com.taobao.trade.platform.api.query.SellerQueryService@queryCount~lQ result_code=1"
                    ),
                },
            ],
        },
        support_limit=2,
    )

    top = bundle.hypotheses[0]

    assert [item.modality for item in top.support] == ["metric", "trace"]
    assert {"metric", "trace"} <= set(top.modalities)
    assert top.contradictions == []


def test_support_excludes_unrelated_non_overlapping_evidence() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_consumer_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_error_qps series_count=1 "
                        "top=[app_group=mainring-offline_host,service=com.taobao.trade.platform.api.query.SellerQueryService:1.0.0,method=queryCount~lQ max=99]"
                    ),
                },
                {
                    "name": "trace_list_client_app_exact",
                    "command": "sf trace list --client-app mainring-offline_host -f json",
                    "returncode": 0,
                    "summary": (
                        "client_name=mainring:mainring-offline_host server_name=tradeplatform3:tp3g3host "
                        "service=com.taobao.trade.platform.api.query.SellerQueryService@queryCount~lQ result_code=1"
                    ),
                },
                {
                    "name": "log_error_list",
                    "command": "sf log error list unrelated -f json",
                    "returncode": 0,
                    "summary": "log_errors count=3 exceptions={'OtherException': 3}",
                },
            ],
        },
        support_limit=4,
    )

    top = bundle.hypotheses[0]

    assert [item.modality for item in top.support] == ["metric", "trace"]
    assert "log" not in top.modalities


def test_app_metadata_summary_does_not_count_as_trace_evidence() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "app_get",
                    "command": "sf app get --app goc-pass -f json",
                    "returncode": 0,
                    "summary": "支持调用链，包含 HSF、SLS、Web 资源",
                },
                {
                    "name": "app_resources",
                    "command": "sf app resources --app goc-pass -f json",
                    "returncode": 0,
                    "summary": "resources include HSF and Web",
                },
            ],
        }
    )

    assert [item.modality for item in bundle.evidence] == ["other", "other"]


def test_bundle_builds_fallback_hypothesis_from_alarm_when_roots_missing() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "自定义监控"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "alarm app=goc-pass title=goc_pass_后端代理(nginx) - 第1条规则 "
                        "metric=1026_spm_19 level=critical"
                    ),
                },
                {
                    "name": "metric_1026_spm_19",
                    "command": "sf metric query 1026_spm_19 -f json",
                    "returncode": 0,
                    "summary": "metric=1026_spm_19 series_count=0 top=",
                },
            ],
        }
    )

    assert bundle.hypotheses
    assert bundle.hypotheses[0].kind == "evidence_alarm"
    assert bundle.hypotheses[0].label == "goc-pass:1026_spm_19"
    assert bundle.hypotheses[0].modalities == ["alarm"]


def test_bundle_filters_zero_only_full_gc_metrics() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "JVM"},
            "root_candidates": [
                {
                    "kind": "pattern_hsf_downstream_timeout",
                    "label": "provider-app downstream_timeout",
                    "score": 6.0,
                    "reason": "provider timeout",
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get 2101062a17840967058096583ed107 -f json",
                    "returncode": 0,
                    "summary": "provider-app timeout result=03 duration_ms=10000",
                },
                {
                    "name": "metric_jvm_gc_fgc_time",
                    "command": "sf metric query jvm_gc_fgc_time -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=jvm_gc_fgc_time series_count=23 top="
                        "[ip=33.42.120.77 min=0,max=0,avg=0,last=0,trend=stable]"
                    ),
                },
            ],
        }
    )

    assert all(item.name != "metric_jvm_gc_fgc_time" for item in bundle.evidence)
    assert all(
        item.name != "metric_jvm_gc_fgc_time"
        for hypothesis in bundle.hypotheses
        for item in hypothesis.support
    )


def test_tddl_span_features_extract_sql_entities() -> None:
    text = "TDDL_QUERY@hm_ascp_charge_rule_sharding_std_0012:charge_statistics_rule\x1aa08741e2 took 12036ms"

    entities = entity_features(text)
    tokens = token_features(text)

    assert entities["sql_ops"] == ["tddl_query"]
    assert entities["sql_dbs"] == ["hm_ascp_charge_rule_sharding_std_0012"]
    assert entities["sql_tables"] == ["charge_statistics_rule"]
    assert entities["sql_ids"] == ["a08741e2"]
    assert "sql_table:charge_statistics_rule" in tokens


def test_ip_and_connection_pool_features_are_tokenized() -> None:
    text = "DruidDataSource get connection timeout from TDDL_CONN on host 33.70.176.208"

    entities = entity_features(text)
    tokens = token_features(text)

    assert entities["ips"] == ["33.70.176.208"]
    assert "ip:33.70.176.208" in tokens
    assert "keyword:connection_pool" in tokens


def test_connection_pool_pattern_outranks_generic_sql_table_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "validation",
                "type": "TDDL",
                "data_ref": "snap",
            },
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": (
                        "host 33.70.176.208 SQL执行失败 because DruidDataSource get connection "
                        "from TDDL connection pool timed out; side query "
                        "TDDL_QUERY@tmi_account_main:tmi_account_main duration_ms=15 result=01/ERR"
                    ),
                }
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_connection_pool"
    assert top.label == "33.70.176.208"


def test_hsf_service_hash_and_slash_extract_method_tokens() -> None:
    entities = entity_features(
        "com.taobao.trade.platform.api.query.SellerQueryService:1.0.0#queryCount~lQ "
        "com.alibaba.demo.ProviderApi:1.0.0/getThing~P"
    )
    tokens = token_features(
        "com.taobao.trade.platform.api.query.SellerQueryService:1.0.0#queryCount~lQ "
        "com.alibaba.demo.ProviderApi:1.0.0/getThing~P"
    )

    assert entities["methods"] == ["getthing", "querycount"]
    assert "method:querycount" in tokens
    assert "method:getthing" in tokens


def test_tddl_trace_span_counts_as_sql_observation() -> None:
    modality = infer_modality(
        "trace_get",
        "sf trace get 213601c617839992682331374e6952 -f json",
        "TDDL_QUERY@intl_bw:resource_lock_setting_his duration=2646ms",
    )

    assert modality == "sql"


def test_jedis_redis_rds_hostname_is_cache_not_database() -> None:
    layer = infer_root_layer(
        "trace_span",
        "(jedis@r-8vb219d10038c044.redis.zhangbei.rds.aliyuncs.com:6379)",
        {},
        "JedisConnectionException SocketTimeoutException during GET",
    )

    assert layer == "cache"


def test_tddl_span_discards_punctuation_table_names() -> None:
    entities = entity_features("TDDL_QUERY@crm_aegean:, duration=18ms")

    assert entities["sql_dbs"] == ["crm_aegean"]
    assert entities["sql_tables"] == []


def test_database_hypothesis_uses_sql_evidence_from_trace_span() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "TDDL", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "pattern_slow_sql",
                    "label": "resource_lock_setting_his",
                    "score": 5.45,
                    "reason": "visible SQL evidence indicates slow query",
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get 213601c617839992682331374e6952 -f json",
                    "returncode": 0,
                    "summary": "TDDL_QUERY@intl_bw:resource_lock_setting_his duration=2646ms",
                }
            ],
        }
    )

    hypothesis = bundle.hypotheses[0]

    assert "sql" in hypothesis.modalities
    assert (
        "database hypothesis has no SQL/RDS evidence in the bundle" not in hypothesis.contradictions
    )


def test_sql_candidate_prioritizes_slow_span_over_many_fast_side_queries() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "TDDL", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "trace_get_aaa",
                    "command": "sf trace get side-trace -f json",
                    "returncode": 0,
                    "summary": (
                        "trace spans=3235 sql_top="
                        "service=TDDL_QUERY@crm_omega_01:global_customer_ext\x1a35199f96 "
                        "duration_ms=23 result=00/OK; "
                        "service=TDDL_QUERY@crm_omega_07:global_customer_ext\x1a35199f96 "
                        "duration_ms=22 result=00/OK "
                        "sql_tables={'global_customer_ext': 149}"
                    ),
                },
                {
                    "name": "trace_get_zzz",
                    "command": "sf trace get alarm-trace -f json",
                    "returncode": 0,
                    "summary": (
                        "trace spans=106 sql_top="
                        "service=TDDL_QUERY@intl_bw:resource_lock_setting_his "
                        "spanClient=2646 result=00/OK "
                        "sql_tables={'resource_lock_setting_his': 1}"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "evidence_sql"
    assert top.label == "resource_lock_setting_his"


def test_repeated_sql_fanout_outranks_generic_hsf_timeout() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": "alarm app=alsc-crm-discount metric=1030_spm_6632",
                },
                {
                    "name": "trace_get_213e004d1784",
                    "command": "sf trace get 2147818e17841241708775250e9ff4 -f json",
                    "returncode": 0,
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
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_tddl_repeated_query_fanout"
    assert top.label.startswith("saas_card_template_relation repeated_sql_fanout")
    assert "thread_pool" not in top.entities.get("keywords", [])
    assert all(
        hypothesis.kind != "pattern_hsf_threadpool_timeout" for hypothesis in bundle.hypotheses
    )


def test_sls_sql_evidence_dedupes_repeated_queries_and_filters_empty_rows() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "TDDL", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "sql_log_error",
                    "label": "TDDL-4614:WS_GENERATE_LOCK:WS_GENERATE_LOCK_UK",
                    "score": 5.0,
                    "reason": "business SLS SQL/TDDL error near alarm window",
                }
            ],
            "evidence": [
                {
                    "name": "sls_sql_query_a",
                    "command": "sf log sls query --query TDDL-4614 -f json",
                    "returncode": 0,
                    "summary": "sql_logs count=30 codes={'TDDL-4614': 30} tables={'WS_GENERATE_LOCK': 30}",
                },
                {
                    "name": "sls_sql_query_b",
                    "command": "sf log sls query --query ERR_EXECUTE_ON_MYSQL -f json",
                    "returncode": 0,
                    "summary": "sql_logs count=30 codes={'TDDL-4614': 30} tables={'WS_GENERATE_LOCK': 30}",
                },
                {
                    "name": "sls_sql_empty",
                    "command": "sf log sls query --query other -f json",
                    "returncode": 0,
                    "summary": "sql_logs count=0 top=",
                },
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "alarm app=manhattan metric=middleware_tddl_write_success_rate "
                        "content=33.27.38.132 TDDL-4614 WS_GENERATE_LOCK"
                    ),
                },
                {
                    "name": "app_get",
                    "command": "sf app get --app manhattan -f json",
                    "returncode": 0,
                    "summary": "app=manhattan resources include TDDL WS_GENERATE_LOCK WS_GENERATE_LOCK_UK",
                },
            ],
        }
    )

    sql_items = [item for item in bundle.evidence if item.name.startswith("sls_sql")]

    assert [item.summary for item in sql_items] == [
        "sql_logs count=30 codes={'TDDL-4614': 30} tables={'WS_GENERATE_LOCK': 30}"
    ]
    assert "sql_logs count=0 top=" not in [item.summary for item in bundle.hypotheses[0].support]
    assert "alarm" in bundle.hypotheses[0].modalities


def test_sql_evidence_without_sql_entity_does_not_fallback_to_project_name() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "test",
                "type": "自定义监控",
                "data_ref": "snap",
            },
            "root_candidates": [],
            "evidence": [
                {
                    "name": "sls_sql_tmi2_performance",
                    "command": (
                        "sf log sls query --uni-key "
                        "ACS#1168425527583626#ACS::SLS::LogStore#cn-shanghai-corp#ali-meri-log:tmi2-performance "
                        "--query Communications -f json"
                    ),
                    "returncode": 0,
                    "summary": (
                        "sql_logs count=30 codes={} tables={} exceptions={} "
                        "trace_ids=['211b269417871029366147141d0e66'] sources=['33.44.96.71']"
                    ),
                }
            ],
        }
    )

    assert all(hypothesis.kind != "evidence_sql" for hypothesis in bundle.hypotheses)
    assert all(hypothesis.label != "ali-meri-log" for hypothesis in bundle.hypotheses)


def test_log_error_raw_path_can_restore_tddl_sql_evidence(tmp_path) -> None:
    raw_log = tmp_path / "log_error_list.json"
    raw_log.write_text(
        json.dumps(
            {
                "errors": [
                    {
                        "exception": "org.springframework.dao.DataAccessResourceFailureException",
                        "trace_id": "0a032a2217849822069777361e9eb2",
                        "stack": (
                            "ERR-CODE: [TDDL-4202][ERR_SQL_QUERY_TIMEOUT] "
                            "Atom:cn-zhangjiakou_i-8vb95unz835pvzktw354_ticket_service_3016, "
                            "Group:TICKET_SERVICE_GROUP, AppName:TICKET_SERVICE_APP, "
                            "file [/home/admin/app/BOOT-INF/classes/mybatis/sqlmapper/ticket/BizTicketMapper.xml] "
                            "SQL: select id from biz_ticket where aliuid = ? order by gmt_create desc"
                        ),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "TDDL", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "evidence_cluster",
                    "label": "ip:33.63.33.23",
                    "score": 5.0,
                    "reason": "multi-signal graph neighborhood",
                }
            ],
            "evidence": [
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app aliyun-customer-servcie -f json",
                    "returncode": 0,
                    "summary": "org.springframework.dao.DataAccessResourceFailureException org.sprin...",
                    "raw_path": str(raw_log),
                }
            ],
        }
    )

    sql_items = [item for item in bundle.evidence if item.modality == "sql"]

    assert sql_items
    assert "TDDL-4202" in sql_items[0].summary
    assert "BIZ_TICKET" in sql_items[0].summary
    assert "THE" not in sql_items[0].summary
    assert bundle.hypotheses[0].label == "biz_ticket"
    assert bundle.hypotheses[0].root_layer == "database"


def test_trace_get_raw_path_can_restore_sql_top_evidence(tmp_path) -> None:
    raw_trace = tmp_path / "trace_get.json"
    raw_trace.write_text(
        json.dumps(
            [
                {
                    "clientName": "(metaq@topic_ascp_vendor_info_change)",
                    "serverName": "aidc-finance-rebate-billing:aidc-finance-rebate-billing_default_host",
                    "service": "MQRecv@topic_ascp_vendor_info_change",
                    "duration": 30,
                    "resultModel": {"code": 1, "name": "ERR"},
                    "resultStr": "01",
                },
                {
                    "clientName": "aidc-finance-rebate-billing:aidc-finance-rebate-billing_default_host",
                    "serverName": "(db@lzd_cfo_mdm)",
                    "service": "TDDL_QUERY@lzd_cfo_mdm:mdm_bank\x1a8c6ee4f7",
                    "duration": 1,
                    "resultModel": {"code": 1, "name": "ERR"},
                    "resultStr": "01",
                },
            ]
        ),
        encoding="utf-8",
    )

    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "trace_get_214136181782",
                    "command": "sf trace get 214136181782 -f json",
                    "returncode": 0,
                    "summary": "trace spans=2 top=MQRecv only",
                    "raw_path": str(raw_trace),
                }
            ],
        }
    )

    assert "sql_top=" in bundle.evidence[0].summary
    assert "TDDL_QUERY@lzd_cfo_mdm:mdm_bank" in bundle.evidence[0].summary
    assert all(hypothesis.kind != "evidence_sql" for hypothesis in bundle.hypotheses)


def test_sls_app_raw_path_can_restore_external_dependency_candidate(tmp_path) -> None:
    raw_app = tmp_path / "sls_app_external.json"
    raw_app.write_text(
        json.dumps(
            {
                "logs": [
                    {
                        "logItem": {
                            "content": (
                                "I/O exception (java.net.NoRouteToHostException) caught when "
                                "processing request to {s}->https://developer.ehuandian.net:443: "
                                "没有到主机的路由 (Host unreachable)"
                            ),
                            "trace": "214f63c217841292866250187e1623",
                        },
                        "sourceMeta": {"__source__": "33.90.138.108"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "hsf_service_method",
                    "label": "consumer->provider:com.alibaba.demo.Api#query",
                    "score": 5.0,
                    "reason": "generic HSF timeout metric labels",
                }
            ],
            "evidence": [
                {
                    "name": "sls_app_ppe_developer_ehuandian",
                    "command": "sf log sls query --query NoRouteToHostException -f json",
                    "returncode": 0,
                    "summary": "app_logs count=0 top=",
                    "raw_path": str(raw_app),
                }
            ],
        }
    )

    assert "label=developer.ehuandian.net" in bundle.evidence[0].summary
    assert bundle.hypotheses[0].kind == "pattern_external_dependency"
    assert bundle.hypotheses[0].label == "developer.ehuandian.net"
    assert bundle.hypotheses[0].root_layer == "service_dependency"


def test_specific_sql_table_hypothesis_beats_generic_slow_sql() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "TDDL", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "pattern_slow_sql",
                    "label": "slow_sql",
                    "score": 5.45,
                    "reason": "visible SQL evidence indicates slow query",
                },
                {
                    "kind": "pattern_slow_sql",
                    "label": "resource_lock_setting_his",
                    "score": 5.45,
                    "reason": "visible SQL evidence indicates TDDL_QUERY@intl_bw:resource_lock_setting_his",
                },
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get 213601c617839992682331374e6952 -f json",
                    "returncode": 0,
                    "summary": "TDDL_QUERY@intl_bw:resource_lock_setting_his duration=2646ms",
                }
            ],
        }
    )

    assert bundle.hypotheses[0].label == "resource_lock_setting_his"


def test_tddl_table_rt_metric_outranks_unrelated_trace_sql_table() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "validation",
                "type": "TDDL",
                "data_ref": "snap",
            },
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "alarm app=disco-develop metric=middleware_tddl_write_rt "
                        "content=[33.6.211.76] tddl写rt 当前值为 42.946ms"
                    ),
                },
                {
                    "name": "metric_middleware_tddl_write_table_rt",
                    "command": (
                        "sf metric query avg by(table,database_name,database_id)"
                        '(middleware_tddl_write_table_rt{app_name="disco-develop"}) -f json'
                    ),
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_tddl_write_table_rt series_count=41 "
                        "top=[table=c2m_portrait_sku_map_product_sku_record "
                        "min=0.3333,max=56.9535,avg=10.8218,last=9.875,trend=rising]; "
                        "[table=tgc_pd_super_link_pre_relation_sku max=5.3238,avg=2.2906]"
                    ),
                },
                {
                    "name": "trace_get_0bf89a901786",
                    "command": "sf trace get 0bf89a901786 -f json",
                    "returncode": 0,
                    "summary": (
                        "trace spans=3767 top=server=disco-develop:disco-develophost "
                        "service=SchedulerXJobExec duration_ms=57677 result=0/OK "
                        "sql_top=client=c2m-supplier-core server=(db@c2m_supplier) "
                        "service=TDDL_QUERY@c2m_supplier:supplier_element_info"
                        "\x1aa106c943 duration_ms=32 result=00/OK"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].label == "c2m_portrait_sku_map_product_sku_record"
    assert bundle.hypotheses[0].root_layer == "database"


def test_tddl_table_success_rate_drop_outranks_plain_qps_rise() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "TDDL", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "metric_middleware_tddl_write_table_qps",
                    "command": "sf metric query sum by(table)(middleware_tddl_write_table_qps) -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_tddl_write_table_qps series_count=80 "
                        "top=[table=ws_bus_task,database_name=alibaba_manhattan "
                        "min=2.1,max=785.8,avg=83.97,last=232.8,trend=rising]"
                    ),
                },
                {
                    "name": "metric_middleware_tddl_write_table_success_rate",
                    "command": (
                        "sf metric query min by(table,database_name,database_id)"
                        "(middleware_tddl_write_table_success_rate) -f json"
                    ),
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_tddl_write_table_success_rate series_count=14 "
                        "top=[table=WS_GENERATE_LOCK,database_name=alibaba_manhattan "
                        "min=0,max=1,avg=0.9231,last=1,trend=falling]"
                    ),
                },
                {
                    "name": "sls_sql_query_a",
                    "command": "sf log sls query --query TDDL-4614 -f json",
                    "returncode": 0,
                    "summary": "sql_logs count=30 codes={'TDDL-4614': 30} tables={'WS_GENERATE_LOCK': 30}",
                },
            ],
        }
    )

    assert bundle.hypotheses[0].label == "ws_generate_lock"
    assert bundle.hypotheses[0].score > bundle.hypotheses[1].score


def test_candidate_top_signals_contribute_hypothesis_modalities() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "evidence_cluster",
                    "label": "app_group:provider-app_host",
                    "score": 5.0,
                    "reason": "multi-signal graph neighborhood",
                    "props": {
                        "top_signals": [
                            {
                                "kind": "metric_series",
                                "label": "middleware_hsf_provider_service_method_rt",
                            },
                            {"kind": "trace_span", "label": "provider-app:provider-app_host"},
                        ]
                    },
                }
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get alarm -f json",
                    "returncode": 0,
                    "summary": "provider-app HSF success rate dropped",
                }
            ],
        }
    )

    hypothesis = bundle.hypotheses[0]

    assert hypothesis.modalities == ["alarm", "metric", "trace"]
    assert (
        "hypothesis has fewer than two concrete evidence modalities"
        not in hypothesis.contradictions
    )


def test_app_log_candidate_prefers_matching_sls_app_support() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "hsf_threadpool_busy",
                    "label": "THREADPOOL_BUSY:33.1.203.42",
                    "score": 5.0,
                    "reason": "HSF provider thread pool busy in application log near alarm window",
                }
            ],
            "evidence": [
                {
                    "name": "trace_get_t1",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "THREADPOOL_BUSY 33.1.203.42 trace span result=03",
                },
                {
                    "name": "sls_app_tradelist_online_THREADPOOL_BUSY",
                    "command": "sf log sls query --query THREADPOOL_BUSY -f json",
                    "returncode": 0,
                    "summary": (
                        "app_logs count=20 error_codes={'THREADPOOL_BUSY': 20} "
                        "provider_ips=['33.1.203.42']"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].support[0].name == "sls_app_tradelist_online_THREADPOOL_BUSY"


def test_stale_db_connection_app_signal_shadows_generic_connection_pattern() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "TDDL", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "stale_db_connection",
                    "label": "stale_jdbc_connection:s_tmi_account_balance_date",
                    "score": 5.0,
                    "reason": "application log shows stale JDBC/MySQL connection failure near alarm window",
                }
            ],
            "evidence": [
                {
                    "name": "sls_app_tmi2_monitor_CommunicationsException",
                    "command": "sf log sls query --query CommunicationsException -f json",
                    "returncode": 0,
                    "summary": (
                        "app_logs count=18 top_signals=[kind=stale_db_connection "
                        "label=stale_jdbc_connection:s_tmi_account_balance_date count=18] "
                        "exceptions=['com.mysql.jdbc.exceptions.jdbc4.CommunicationsException'] "
                        "stale_packet_ms=['176503']"
                    ),
                }
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "stale_db_connection"
    assert bundle.hypotheses[0].support[0].name == "sls_app_tmi2_monitor_CommunicationsException"
    assert all(
        hypothesis.kind != "pattern_connection_pool"
        or hypothesis.score < bundle.hypotheses[0].score
        for hypothesis in bundle.hypotheses
    )


def test_stale_db_connection_outranks_generic_ip_evidence_cluster() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "test",
                "type": "自定义监控",
                "data_ref": "snap",
            },
            "root_candidates": [
                {
                    "kind": "evidence_cluster",
                    "label": "ip:33.44.96.71",
                    "score": 5.0,
                    "reason": "multi-signal graph neighborhood",
                },
                {
                    "kind": "stale_db_connection",
                    "label": "stale_jdbc_connection:mysql",
                    "score": 5.0,
                    "reason": "application log shows stale JDBC/MySQL connection failure near alarm window",
                    "props": {
                        "trace_ids": ["213dd8f217871035517475239d0d4d"],
                        "stale_packet_ms": ["176503"],
                        "sources": ["33.44.96.71"],
                    },
                },
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get snap -f json",
                    "returncode": 0,
                    "summary": "alarm app=tmi2 metric=sql_fail content=33.44.96.71 213dd8f217871035517475239d0d4d",
                },
                {
                    "name": "metric_middleware_tddl_read_qps",
                    "command": "sf metric query middleware_tddl_read_qps -f json",
                    "returncode": 0,
                    "summary": "metric=middleware_tddl_read_qps top=[ip=33.44.96.71,max=968.9,trend=rising]",
                },
                {
                    "name": "sls_app_tmi2_monitor_CommunicationsException",
                    "command": "sf log sls query --query CommunicationsException -f json",
                    "returncode": 0,
                    "summary": (
                        "app_logs count=18 top_signals=[kind=stale_db_connection "
                        "label=stale_jdbc_connection:mysql count=18] "
                        "trace_ids=['213dd8f217871035517475239d0d4d'] sources=['33.44.96.71']"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "stale_db_connection"
    assert bundle.hypotheses[0].label == "stale_jdbc_connection:mysql"


def test_structured_hsf_app_log_threadpool_signal_outranks_connection_pool_noise() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "sls_app_tradelist_online_THREADPOOL_BUSY",
                    "command": "sf log sls query --query THREADPOOL_BUSY -f json",
                    "returncode": 0,
                    "summary": (
                        "app_logs count=30 top_signals=[kind=hsf_threadpool_busy "
                        "label=THREADPOOL_BUSY:33.1.203.42 count=23] "
                        "provider_ips=['33.1.203.42']"
                    ),
                },
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app trade-contract -f json",
                    "returncode": 0,
                    "summary": (
                        "log_errors count=30 root_hints={'CommunicationsException': 18, "
                        "'DruidDataSource': 12} exceptions={'com.mysql.jdbc.exceptions.jdbc4."
                        "CommunicationsException': 18}"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "hsf_threadpool_busy"
    assert bundle.hypotheses[0].label == "THREADPOOL_BUSY:33.1.203.42"
    assert "service-dependency hypothesis is not directly backed by trace evidence" not in (
        bundle.hypotheses[0].contradictions
    )
    assert all(
        hypothesis.kind != "pattern_connection_pool"
        or hypothesis.score < bundle.hypotheses[0].score
        for hypothesis in bundle.hypotheses
    )


def test_bundle_uses_nonempty_app_log_summary_when_raw_file_is_empty(tmp_path) -> None:
    raw_path = tmp_path / "sls_app_fin_fund_solution_THREADPOOL_BUSY.json"
    raw_path.write_text("[]", encoding="utf-8")
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "hsf_threadpool_busy",
                    "label": "THREADPOOL_BUSY:33.62.98.154",
                    "score": 5.0,
                    "reason": "HSF provider thread pool busy in application log near alarm window",
                }
            ],
            "evidence": [
                {
                    "name": "sls_app_fin_fund_solution_THREADPOOL_BUSY",
                    "command": "sf log sls query --query THREADPOOL_BUSY -f json",
                    "raw_path": str(raw_path),
                    "summary": (
                        "app_logs count=30 top_signals=[kind=hsf_threadpool_busy "
                        "label=THREADPOOL_BUSY:33.62.98.154 count=10] "
                        "provider_ips=['33.62.98.154'] sources=['33.39.200.234']"
                    ),
                },
                {
                    "name": "trace_get_t1",
                    "command": "sf trace get t1 -f json",
                    "summary": (
                        "trace spans=2 hsf_error_top=client=fin-fund:fin-fundhost "
                        "server=fin-fund-solution:fin-fund-solutionhost "
                        "service=FundSolutionProxyFacade@collect failures=2 "
                        "max_duration_ms=3849 result_codes={'03/TIMEOUT': 2} "
                        "provider_ips={'33.39.200.234': 2}; "
                        "client=fin-fund-solution:fin-fund-solutionhost "
                        "server=fin-cif:fin-cif_hz_host "
                        "service=CifBankAccountFacade@query failures=1 "
                        "max_duration_ms=3788 result_codes={'02/RPC_ERROR': 1} "
                        "provider_ips={'33.62.98.154': 1}"
                    ),
                },
            ],
        }
    )

    assert bundle.evidence[0].name == "sls_app_fin_fund_solution_THREADPOOL_BUSY"
    assert "app_logs count=30" in bundle.evidence[0].summary
    assert bundle.hypotheses[0].kind == "hsf_threadpool_busy"
    assert bundle.hypotheses[0].label == "THREADPOOL_BUSY:33.62.98.154"


def test_empty_list_fields_do_not_make_nonempty_sls_app_observation_empty() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "test",
                "type": "自定义监控",
                "data_ref": "snap",
            },
            "root_candidates": [
                {
                    "kind": "app_log_limit",
                    "label": "UMP_SENTINEL_BLOCK:queryItemSkuPrice",
                    "score": 5.0,
                    "reason": "application log shows Sentinel/UMP limiting near alarm window",
                }
            ],
            "evidence": [
                {
                    "name": "sls_app_spo_log_UMP_SENTINEL_BLOCK",
                    "command": "sf log sls query --query UMP_SENTINEL_BLOCK -f json",
                    "returncode": 0,
                    "summary": (
                        "app_logs count=24 error_codes={'UMP_SENTINEL_BLOCK': 24} "
                        "provider_ips=[] trace_ids=[] sources=['33.5.46.135']"
                    ),
                }
            ],
        }
    )

    assert [item.name for item in bundle.evidence] == ["sls_app_spo_log_UMP_SENTINEL_BLOCK"]


def test_hypotheses_sort_before_limit() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "weak-a",
                    "score": 1.0,
                    "reason": "abnormal trace span",
                },
                {
                    "kind": "trace_span",
                    "label": "weak-b",
                    "score": 1.1,
                    "reason": "abnormal trace span",
                },
                {
                    "kind": "hsf_threadpool_busy",
                    "label": "THREADPOOL_BUSY:33.1.203.42",
                    "score": 5.0,
                    "reason": "HSF provider thread pool busy in application log near alarm window",
                },
            ],
            "evidence": [
                {
                    "name": "sls_app_hit",
                    "command": "sf log sls query --query THREADPOOL_BUSY -f json",
                    "returncode": 0,
                    "summary": "app_logs count=10 error_codes={'THREADPOOL_BUSY': 10} provider_ips=['33.1.203.42']",
                }
            ],
        },
        hypothesis_limit=1,
    )

    assert bundle.hypotheses[0].kind == "hsf_threadpool_busy"


def test_strong_limit_pattern_can_outrank_downstream_sql_evidence() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "trace_get_t1",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "TDDL_QUERY@db:mpm_margin_reduce_config\x1asqlid duration_ms=20",
                },
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app demo -f json",
                    "returncode": 0,
                    "summary": (
                        "root_hints={'BlockException': 4} "
                        "exceptions={'com.alibaba.csp.sentinel.slots.block.BlockException': 4}"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "pattern_limit"


def test_strong_limit_pattern_outranks_repeated_sql_fanout_for_hsf() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "trace_get_t1",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": (
                        "trace spans=40 hsf_error_top=client=mobile-messages-service:host "
                        "server=mobile-common-service:host service=MobileUserSettingService@selectByQuery "
                        "failures=3 max_duration_ms=1 result_codes={'01/RuntimeException': 3} "
                        "sql_tables={'im_tag_relation': 30} sql_top=client=mobile-common-service:host "
                        "server=(db@mobile) service=TDDL_QUERY@mobile:im_tag_relation\x1asqlid "
                        "duration_ms=12 result=00/OK"
                    ),
                },
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app mobile-messages-service -f json",
                    "returncode": 0,
                    "summary": (
                        "root_hints={'SentinelBlockException': 4} "
                        "exceptions={'com.alibaba.csp.sentinel.slots.block.BlockException': 4}"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "pattern_limit"
    assert all(
        hypothesis.kind != "pattern_tddl_repeated_query_fanout"
        or hypothesis.score < bundle.hypotheses[0].score
        for hypothesis in bundle.hypotheses
    )


def test_bundle_promotes_topology_path_as_trace_backed_hypothesis() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "nodes": [
                {"id": "trace:t1", "kind": "trace", "label": "t1"},
                {
                    "id": "span:t1:0.1",
                    "kind": "span",
                    "label": "0.1",
                    "props": {"duration_ms": 3001, "result_code": "03"},
                },
                {"id": "endpoint:consumer:host", "kind": "endpoint", "label": "consumer:host"},
                {"id": "endpoint:provider:host", "kind": "endpoint", "label": "provider:host"},
                {
                    "id": "service:com.demo.ProviderApi@getThing",
                    "kind": "service",
                    "label": "com.demo.ProviderApi@getThing",
                },
            ],
            "edges": [
                {"source": "trace:t1", "rel": "HAS_SPAN", "target": "span:t1:0.1"},
                {"source": "span:t1:0.1", "rel": "CLIENT", "target": "endpoint:consumer:host"},
                {"source": "span:t1:0.1", "rel": "SERVER", "target": "endpoint:provider:host"},
                {
                    "source": "span:t1:0.1",
                    "rel": "INVOKES",
                    "target": "service:com.demo.ProviderApi@getThing",
                },
            ],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "command": "sf metric query middleware_hsf_consumer_service_method_rt -f json",
                    "returncode": 0,
                    "summary": "consumer to provider RT reached 3001ms",
                }
            ],
        }
    )

    topology_items = [item for item in bundle.evidence if item.modality == "topology"]

    assert topology_items
    assert bundle.hypotheses[0].kind == "topology_trace_path"
    assert (
        "service-dependency hypothesis is not directly backed by trace evidence"
        not in bundle.hypotheses[0].contradictions
    )


def test_topology_evidence_does_not_boost_source_trace_candidate() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "consumer:consumer_host",
                    "score": 5.0,
                    "reason": "source graph trace span candidate",
                    "props": {"server": "provider:provider_host"},
                }
            ],
            "nodes": [
                {"id": "trace:t1", "kind": "trace", "label": "t1"},
                {
                    "id": "span:t1:0.1",
                    "kind": "span",
                    "label": "0.1",
                    "props": {"duration_ms": 3001, "result_code": "03"},
                },
                {
                    "id": "endpoint:consumer:host",
                    "kind": "endpoint",
                    "label": "consumer:consumer_host",
                },
                {
                    "id": "endpoint:provider:host",
                    "kind": "endpoint",
                    "label": "provider:provider_host",
                },
                {
                    "id": "service:com.demo.ProviderApi@getThing",
                    "kind": "service",
                    "label": "com.demo.ProviderApi@getThing",
                },
            ],
            "edges": [
                {"source": "trace:t1", "rel": "HAS_SPAN", "target": "span:t1:0.1"},
                {"source": "span:t1:0.1", "rel": "CLIENT", "target": "endpoint:consumer:host"},
                {"source": "span:t1:0.1", "rel": "SERVER", "target": "endpoint:provider:host"},
                {
                    "source": "span:t1:0.1",
                    "rel": "INVOKES",
                    "target": "service:com.demo.ProviderApi@getThing",
                },
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "consumer to provider timeout",
                }
            ],
        }
    )

    hypothesis = bundle.hypotheses[0]

    assert hypothesis.kind == "trace_span"
    assert "topology" not in hypothesis.modalities
    assert all(item.modality != "topology" for item in hypothesis.support)


def test_opaque_event_id_does_not_outrank_redis_trace_root() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "validation",
                "type": "OTHER",
                "data_ref": "snap",
            },
            "root_candidates": [
                {"kind": "event", "label": "e83057263c3c89e1ea5eb535133c4a7a", "score": 5.0},
                {
                    "kind": "log_error",
                    "label": "com.amap.aos.http.client.core.HttpClientException",
                    "score": 4.8,
                },
                {
                    "kind": "trace_span",
                    "label": "amap-aos-order-data-service:amap-aos-order-data-service_na61_host",
                    "score": 4.5,
                },
                {
                    "kind": "trace_span",
                    "label": "(jedis@r-8vb219d10038c044.redis.zhangbei.rds.aliyuncs.com:6379)",
                    "score": 4.0,
                    "reason": "Redis GET timed out in trace",
                },
            ],
            "evidence": [
                {
                    "name": "event_change_list",
                    "command": "sf event change list --app demo -f json",
                    "returncode": 0,
                    "summary": "changes=3 top=e83057263c3c89e1ea5eb535133c4a7a",
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get 212a8ebb1783 -f json",
                    "returncode": 0,
                    "summary": (
                        "JedisConnectionException SocketTimeoutException GET "
                        "r-8vb219d10038c044.redis.zhangbei.rds.aliyuncs.com:6379"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].label == "r-8vb219d10038c044"
    assert bundle.hypotheses[0].root_layer == "cache"


def test_app_publish_data_quality_pattern_outranks_plain_data_quality() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "OTHER"},
            "root_candidates": [
                {
                    "kind": "event",
                    "label": "2992969898",
                    "score": 5.0,
                    "reason": "changefree publish event near alarm",
                },
                {
                    "kind": "log_error",
                    "label": "com.alibaba.mp.fund.common.exception.MpfBizException",
                    "score": 4.0,
                    "reason": "NO_QUALIFICATION business error",
                },
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "summary": "alarm app=mp-fund content=DP_CREATE NO_QUALIFICATION 定品未创建",
                },
                {
                    "name": "event_changefree_query",
                    "command": 'sf event query --query \'{} | appName = "mp-fund" and source = "changefree"\'',
                    "summary": (
                        "events count=1 top=sourceProduct=CHANGEFREE_EXE "
                        "change_system=normandy change_type=APP_PUBLISH "
                        "change_summary=应用mp-fund部署production环境 "
                        "deploy_id=157962710 deploy_version=234485125 batch=2/3"
                    ),
                },
                {
                    "name": "log_error_list",
                    "summary": (
                        "log_errors count=10 exceptions="
                        "{'com.alibaba.mp.fund.common.exception.MpfBizException': 3} "
                        "NO_QUALIFICATION"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_app_publish_data_quality"
    assert top.root_layer == "change"
    assert "deploy_id=157962710" in top.label
    assert not top.contradictions


def test_downstream_offline_change_pattern_outranks_threadpool_symptom() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "JVM"},
            "root_candidates": [
                {
                    "kind": "log_error",
                    "label": "com.taobao.hsf.exception.HSFTimeOutException",
                    "score": 4.0,
                    "reason": "THREADPOOL_BUSY timeout symptom",
                }
            ],
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
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "summary": (
                        "metric=middleware_hsf_consumer_service_method_error_qps "
                        "series_count=1 top=[app_group=prophet-service_hz_host,"
                        "remote_app_name=freight-template,method=batchQueryProductLogisticsCarryInfos~P,"
                        "max=5.317,trend=rising]"
                    ),
                },
                {
                    "name": "log_error_list",
                    "summary": (
                        "log_errors count=10 root_hints={'THREADPOOL_BUSY': 2} "
                        "exceptions={'com.taobao.hsf.exception.HSFTimeOutException': 32}"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_downstream_offline_change"
    assert top.root_layer == "change"
    assert "freight-template" in top.label
    assert not top.contradictions


def test_direct_business_system_error_outranks_background_offline_change() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "pattern_downstream_offline_change",
                    "label": "cnortools-center change_id=2819931362 offline_capacity_change",
                    "score": 8.7,
                    "reason": "visible downstream offline/config change overlaps HSF timeout evidence",
                },
                {
                    "kind": "business_system_error",
                    "label": (
                        "OfficialDeliveryOrderService.consignByOfficialDeliveryOrder "
                        "SYSTEM_ERROR 电子面单账户余额不足"
                    ),
                    "score": 5.0,
                    "reason": (
                        "application log shows HSF/business handler returned "
                        "SYSTEM_ERROR/BIZ_ERROR near alarm window"
                    ),
                },
            ],
            "evidence": [
                {
                    "name": "sls_app_cbu_dp_err_proj_SYSTEM_ERROR",
                    "summary": (
                        "app_logs count=30 error_codes={'SYSTEM_ERROR': 30} "
                        'top_signals=["kind=business_system_error '
                        "label=OfficialDeliveryOrderService.consignByOfficialDeliveryOrder "
                        'SYSTEM_ERROR 电子面单账户余额不足 count=30"]'
                    ),
                },
                {
                    "name": "event_change_list",
                    "summary": (
                        "events count=5 top=id=2819931362 system=baas-center "
                        "type=RESOURCE_MODIFY title=变更资源-OpenSearch result=变更成功"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps "
                        "top=[app_group=cnortools-centerhost,"
                        "service=com.alibaba.shared.carriage.delivery.service."
                        "OfficialDeliveryOrderService:1.0.0,"
                        "method=consignByOfficialDeliveryOrder~O,max=0.133,trend=rising]"
                    ),
                },
                {
                    "name": "alarm_get",
                    "summary": (
                        "alarm app=cnortools-center "
                        "OfficialDeliveryOrderService consignByOfficialDeliveryOrder"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind in {"business_system_error", "pattern_data_quality"}
    assert "OfficialDeliveryOrderService" in top.label
    assert "电子面单账户余额不足" in top.label or top.kind == "pattern_data_quality"


def test_event_raw_payload_can_outrank_trace_host_anomaly(tmp_path) -> None:
    raw_path = tmp_path / "event_query_app.json"
    raw_path.write_text(
        json.dumps(
            [
                {
                    "stream": {
                        "sourceProduct": "ECS",
                        "eventLevel": "critical",
                        "instanceId": '["i-8vbiyp6wvmcp36j72a5u"]',
                        "type": "acs.ecs[ecs:CloudMonitor:Instance[SystemMaintenance.Redeploy:Avoided]]",
                    },
                    "values": [
                        [
                            1784755034,
                            {
                                "data": {
                                    "alertRuleName": "local_disk_nc_down_hardware_error",
                                    "eventStatus": "Avoided",
                                    "instanceId": "i-8vbiyp6wvmcp36j72a5u",
                                    "privateIpAddress": ["33.33.183.119"],
                                    "reason": "The host machine has potential failure risks;Memory error",
                                },
                                "id": "E85057CC3FDC0AEE3F1DB5C4B634AB139BA8BEA7-CMS",
                                "time": "2026-07-22T21:17:14.000Z",
                            },
                        ]
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "OTHER"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "fin-agreement-center:fin-agreement-center_hz_host",
                    "score": 5.0,
                    "reason": "abnormal trace span",
                },
                {
                    "kind": "event",
                    "label": "E85057CC3FDC0AEE3F1DB5C4B634AB139BA8BEA7-CMS",
                    "score": 5.0,
                    "reason": "infrastructure/runtime event near alarm window",
                },
            ],
            "evidence": [
                {
                    "name": "event_query_app",
                    "command": 'sf event query -Q {appName="fin-agreement-center"} -f json',
                    "returncode": 0,
                    "raw_path": str(raw_path),
                    "summary": '[{"stream": {"sourceProduct": "ECS"}}]',
                },
                {
                    "name": "topology_trace_path",
                    "returncode": 0,
                    "summary": (
                        "trace t1 topology path: palisade:palisadehost -> "
                        "fin-agreement-center:fin-agreement-center_hz_host "
                        "com.alibaba.b2b.fin.agreement.api.AgreementFacade@query~P "
                        "10002ms rc=03 server_ip=33.44.187.176"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_infra_event"
    assert top.label == "i-8vbiyp6wvmcp36j72a5u hardware_memory_fault"
    assert top.root_layer == "infrastructure"
    assert top.contradictions == []
    assert any("Memory error" in item.summary for item in top.support)


def test_notify_business_failure_pattern_outranks_cache_side_spans() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "METAQ"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "(tair@3e25595fc568400e:tair.mdb.mlsc.wdk)",
                    "score": 5.0,
                    "reason": "abnormal Tair GET span",
                }
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": "alarm app=wdk-crowd-center metric=middleware_notify_receive_success_rate notify消费成功率 60%",
                },
                {
                    "name": "metric_middleware_notify_receive_success_rate",
                    "command": "sf metric query middleware_notify_receive_success_rate -f json",
                    "returncode": 0,
                    "summary": "metric=middleware_notify_receive_success_rate app_group=wdk-crowd-centerhost trend=falling",
                },
                {
                    "name": "trace_list_server_app_exact",
                    "command": "sf trace list --serverName wdk-crowd-centerhost -f json",
                    "returncode": 0,
                    "summary": (
                        "trace spans=8 top=server=wdk-crowd-center:wdk-crowd-centerhost "
                        "service=Notify@recv~BytesMessage:TC_REFUND_DISPUTE:RP-REFUND-AGRT-APPLIED:"
                        "P-RP3-DEFAULT-GID result=1"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_notify_business_failure"
    assert top.label == "wdk-crowd-center TC_REFUND_DISPUTE business_consume_failure"
    assert top.root_layer == "application"
    assert top.contradictions == []


def test_config_mq_failure_pattern_outranks_metric_spike() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "OTHER", "data_ref": "snap"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "alarm app=lazada-credit-core-s "
                        "metric=middleware_metaq_receive_success_rate metaq消费成功率异常"
                    ),
                },
                {
                    "name": "metric_middleware_metaq_receive_qps",
                    "command": "sf metric query middleware_metaq_receive_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_metaq_receive_qps series_count=1 "
                        "top=[app_group=lazada-credit-core-s-rg-sg-prodhost max=3.4 trend=rising]"
                    ),
                },
                {
                    "name": "event_changefree_query",
                    "command": "sf event query -Q appName=lazada-credit-core-s -f json",
                    "returncode": 0,
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
                    "command": "sf trace list --serverName lazada-credit-core-s -f json",
                    "returncode": 0,
                    "summary": (
                        "trace error_top=service=MQRecv@GL_CREDIT-INNER-NOTIFY-TOPIC_AIPAY_PH002:"
                        "CID_GL_CREDIT_INNER_NOTIFY_LISTENER_AIPAY_PH002:LOAN_DISCOUNT "
                        "result=1/BIZ_ERROR"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_config_mq_failure"
    assert "result.notice.config" in top.label
    assert top.root_layer == "change"


def test_mdm_master_data_pattern_outranks_neighbor_hsf_metrics() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "hsf_service_method",
                    "label": (
                        "billinghost->ascp-vendor:"
                        "com.alibaba.ascp.vendor.api.VendorFinanceReadService:1.0.0#querySingle~LLI"
                    ),
                    "score": 7.2,
                    "reason": "neighbor HSF metric label",
                }
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "alarm app=aidc-finance-rebate-billing "
                        "content=ASCPBusinessPartnerFacade sync 成功率 当前值为: 0, 失败数 当前值为: 1"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get 2141361817823859087711149d0abe -f json",
                    "returncode": 0,
                    "summary": (
                        "error_top=server=aidc-finance-rebate-billing:aidc-finance-rebate-billing_default_host "
                        "service=MQRecv@topic_ascp_vendor_info_change:CID:UPDATE result=01/ERR/BIZ_ERROR "
                        "sql_tables={'mdm_bank': 4, 'vendor_finance': 4} "
                        "sql_top=client=lzd-cfo-mdm:lzd-cfo-mdm__host server=(db@lzd_cfo_mdm) "
                        "service=TDDL_QUERY@lzd_cfo_mdm:mdm_bank\x1a8c6ee4f7"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_consumer_service_method_success_rate",
                    "command": "sf metric query middleware_hsf_consumer_service_method_success_rate -f json",
                    "returncode": 0,
                    "summary": "metric=middleware_hsf_consumer_service_method_success_rate series_count=1 top=[service=com.lazada.cfo.mdm.ASCPBusinessPartnerFacade:1.0.0,method=sync~A trend=falling]",
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_mdm_master_data_missing"
    assert top.root_layer == "application"
    assert "mdm_bank" in top.label


def test_tddl_read_traffic_source_pattern_outranks_table_only_sql_candidate() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "TDDL"},
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": "alarm app=wdk-suppliercore metric=middleware_tddl_read_qps tddl读qps 当前值为 804",
                },
                {
                    "name": "metric_middleware_tddl_read_qps",
                    "command": "sf metric query middleware_tddl_read_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_tddl_read_qps series_count=1 "
                        "top=[app_group=wdk-suppliercorehost max=830,trend=rising]"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get 0bab0c2f17823103163387239d0987 -f json",
                    "returncode": 0,
                    "summary": (
                        "trace spans=4000 sql_tables={'wdk_merchant_store_sku': 937, 'wdk_supplier': 409} "
                        "sql_top=client=wdk-suppliercore:wdk-suppliercorehost server=(db@wdk_supplierprod) "
                        "service=TDDL_QUERY@wdk_supplierprod:wdk_supplier\x1a65ee5e9a "
                        "top=client=wdk-item-controller:wdk-item-controllerhost "
                        "server=wdk-suppliercore:wdk-suppliercorehost "
                        "service=com.wdk.suppliercore.client.service.WdkSupplierQueryService@getSupplierByCode~SS "
                        "duration_ms=4 result=01/ERR"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_tddl_read_traffic_source"
    assert "wdk-item-controller -> wdk-suppliercore" in top.label
    assert "wdk_supplier" in top.label
    assert top.root_layer == "database"


def test_cache_trace_with_timeout_context_uses_evidence_count_to_break_tie() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "validation",
                "type": "OTHER",
                "data_ref": "snap",
            },
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "order-service:order-service_host",
                    "score": 4.5,
                    "props": {"evidence_count": 31, "result_code": "03"},
                },
                {
                    "kind": "trace_span",
                    "label": "(jedis@r-8vbb6b2cba5dace4.redis.zhangbei.rds.aliyuncs.com:6379)",
                    "score": 4.0,
                    "props": {"evidence_count": 1, "result_code": "00"},
                },
                {
                    "kind": "trace_span",
                    "label": "(jedis@r-8vb219d10038c044.redis.zhangbei.rds.aliyuncs.com:6379)",
                    "score": 4.0,
                    "props": {"evidence_count": 14, "result_code": "00"},
                },
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": "price-center getOrderDetailV2 errors",
                },
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app price-center -f json",
                    "returncode": 0,
                    "summary": "java.net.SocketTimeoutException query timeout during order detail",
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get 212a8ebb17833075682686436d0a13 -f json",
                    "returncode": 0,
                    "summary": "order-service calls Redis and returns HSF error",
                },
            ],
        }
    )

    assert (
        bundle.hypotheses[0].label
        == "(jedis@r-8vb219d10038c044.redis.zhangbei.rds.aliyuncs.com:6379)"
    )
    assert bundle.hypotheses[0].root_layer == "cache"


def test_cache_trace_bonus_does_not_match_order_data_service_name() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "validation",
                "type": "OTHER",
                "data_ref": "snap",
            },
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "amap-aos-order-data-service:amap-aos-order-data-service_na61_host",
                    "score": 4.5,
                    "props": {"evidence_count": 31, "result_code": "03"},
                },
                {
                    "kind": "trace_span",
                    "label": "(jedis@r-8vb219d10038c044.redis.zhangbei.rds.aliyuncs.com:6379)",
                    "score": 4.0,
                    "props": {"evidence_count": 14, "result_code": "00"},
                },
            ],
            "evidence": [
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app price-center -f json",
                    "returncode": 0,
                    "summary": "java.net.SocketTimeoutException query timeout during order detail",
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get 212a8ebb17833075682686436d0a13 -f json",
                    "returncode": 0,
                    "summary": "amap-aos-order-data-service and Redis are both present",
                },
            ],
        }
    )

    service = next(
        item
        for item in bundle.hypotheses
        if item.label == "amap-aos-order-data-service:amap-aos-order-data-service_na61_host"
    )
    redis = next(item for item in bundle.hypotheses if item.root_layer == "cache")

    assert redis.score > service.score


def test_metaq_business_failure_outranks_metric_spike_for_metaq_success_alarm() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "METAQ", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "pattern_mq_spike",
                    "label": "alsc_eloan_credit_apply_result_message",
                    "score": 8.1,
                    "reason": "visible MetaQ/RocketMQ metric series indicates message volume spike",
                },
                {
                    "kind": "metaq_business_failure",
                    "label": (
                        "metaq_message:business_consume_failure:"
                        "couponCode=7319787376028756401:BizException"
                    ),
                    "score": 4.8,
                    "reason": (
                        "application log shows MetaQ message consumption failed in business "
                        "handler near alarm window"
                    ),
                    "props": {
                        "trace_ids": ["212c4c8e17862075418048755d0f3a"],
                        "services": ["LoanCouponDomainServiceImpl.processFunderCouponChange"],
                    },
                },
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": "alarm metric=middleware_metaq_receive_success_rate metaq消费成功率异常",
                },
                {
                    "name": "sls_app_mq",
                    "command": "sf log sls query --query msgId -f json",
                    "returncode": 0,
                    "summary": (
                        "app_logs count=30 top_signals=['kind=metaq_business_failure "
                        "label=metaq_message:business_consume_failure:"
                        "couponCode=7319787376028756401:BizException']"
                    ),
                },
                {
                    "name": "metric_middleware_metaq_receive_success_rate",
                    "command": "sf metric query metaq -f json",
                    "returncode": 0,
                    "summary": "metric=middleware_metaq_receive_success_rate trend=falling",
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "metaq_business_failure"
    assert bundle.hypotheses[0].root_layer == "application"


def test_instance_count_drop_offline_change_outranks_tair_side_span() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "OTHER", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "(tair@2dbea1497c924275:ldbicbu)",
                    "score": 6.0,
                    "reason": "Tair GET result_code=-3998 appears in a sampled trace",
                }
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "共有1条数据触发报警 [mtee3.cn.prodhost,unsh.EA119] "
                        "机器数量 当前值为:60 同比下跌:83.333%"
                    ),
                },
                {
                    "name": "trace_get",
                    "command": "sf trace get t1 -f json",
                    "returncode": 0,
                    "summary": "trace top=client=mtee3 server=(tair@2dbea1497c924275:ldbicbu) result=-3998",
                },
                {
                    "name": "event_change_list",
                    "command": "sf event change list --app mtee3 --infra -f json",
                    "returncode": 0,
                    "summary": {
                        "business_changes": [
                            {
                                "id": "100",
                                "change_type": "CONFIG_PUSH",
                                "title": "mtee3-普通配置恢复",
                                "result": "变更成功",
                                "system": "preplan2",
                                "end_time": "2026-06-11 20:07:52",
                            },
                            {
                                "id": "2843585453",
                                "change_type": "OFFLINE_HOST",
                                "title": "正式-机器下线",
                                "result": "变更成功",
                                "system": "normandy-director",
                                "end_time": "2026-06-11 22:20:36",
                            },
                        ]
                    },
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_instance_count_drop_offline_change"
    assert top.root_layer == "change"
    assert "normandy_offline_capacity_drop" in top.label
    assert {"alarm", "event"} <= set(top.modalities)


def test_metaq_broker_failure_outranks_hsf_timeout_side_effect() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "log_error",
                    "label": "com.taobao.hsf.exception.HSFTimeOutException",
                    "score": 4.0,
                    "reason": "concrete error log near alarm",
                }
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": "alarm app=idle-cco metric=app_error_cnt Java异常错误数",
                },
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app idle-cco -f json",
                    "returncode": 0,
                    "summary": (
                        "log_errors count=10 "
                        "broker_hints={'fetch name server address exception': 1, "
                        "'RemotingConnectException connect to <33.9.126.179:10909> failed': 1, "
                        "'broker[trade_sub_notify_metaq-zoneB-11] not exist': 1, "
                        "'updateConsumeOffsetToBroker': 1} "
                        "exceptions={'HSFTimeOutException': 9, 'MQClientException': 1}"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "pattern_metaq_broker_failure"
    assert "trade_sub_notify_metaq-zoneB-11" in bundle.hypotheses[0].label
    assert bundle.hypotheses[0].root_layer == "message_queue"


def test_metaq_duplicate_update_conflict_outranks_generic_mq_spike() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "METAQ"},
            "root_candidates": [],
            "evidence": [
                {
                    "name": "metric_middleware_metaq_receive_qps",
                    "command": "sf metric query middleware_metaq_receive_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_metaq_receive_qps series_count=1 "
                        "top=[topic=LOGISTICS_ON_DEMAND_TRACE_TOPIC,trend=rising]"
                    ),
                },
                {
                    "name": "sls_app_tt_logistics_cs_daemon",
                    "command": "sf log sls query --query 2150466d17806513712328452e0c86 -f json",
                    "returncode": 0,
                    "summary": (
                        'app_logs count=2 top_signals=["kind=metaq_duplicate_update_conflict '
                        "label=LOGISTICS_ON_DEMAND_TRACE_TOPIC:duplicate_update_conflict:"
                        "GOT:mailNo=YT1134183699405 count=1 exceptions=['BadRequestException'] "
                        "trace_ids=['2150466d17806513712328452e0c86']\"]"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "pattern_metaq_duplicate_update_conflict"
    assert bundle.hypotheses[0].root_layer == "application"


def test_auth_session_failure_outranks_normal_cache_side_span() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "test",
                "type": "自定义监控",
                "data_ref": "snap",
            },
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "goc-pass:goc-passhost",
                    "score": 4.0,
                    "reason": "abnormal trace span",
                    "props": {
                        "trace_id": "8ccd75d217815846928741544e77e6",
                        "server": "goc-pass:goc-passhost",
                        "service": "https://tr.alibaba-inc.com/gocFaultDef/innerApi/v2/incident/scenarios/level/defs",
                        "result_code": "401",
                        "duration_ms": 180,
                        "evidence_count": 10,
                    },
                },
                {
                    "kind": "trace_span",
                    "label": "(jedis@r-8vbhsxsii2vswr9bj2.redis.zhangbei.rds.aliyuncs.com:6379)",
                    "score": 4.0,
                    "reason": "abnormal trace span",
                    "props": {
                        "trace_id": "8ccd75d217815853910455089e77e6",
                        "service": "PIPELINESYNC:r-8vbhsxsii2vswr9bj2.redis.zhangbei.rds.aliyuncs.com:6379",
                        "result_code": "00",
                        "duration_ms": 0,
                        "evidence_count": 1,
                    },
                },
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "alarm app=goc-pass title=goc_pass_后端代理(nginx) metric=1026_spm_19 "
                        "content=[gocFaultDef] 失败数 当前值为 30"
                    ),
                },
                {
                    "name": "trace_list_server_app_exact",
                    "command": "sf trace list --filter serverName=goc-pass resultType!=1 -f json",
                    "returncode": 0,
                    "summary": (
                        "server=goc-pass:goc-passhost service=https://tr.alibaba-inc.com/"
                        "gocFaultDef/innerApi/v2/incident/scenarios/level/defs "
                        "duration_ms=180 result=401/UNAUTHORIZED/RPC_ERROR server_ip=33.102.22.35"
                    ),
                },
                {
                    "name": "trace_get_8ccd75d21781",
                    "command": "sf trace get 8ccd75d217815846928741544e77e6 -f json",
                    "returncode": 0,
                    "summary": (
                        "trace spans=3 error_top=server=goc-pass:goc-passhost "
                        "service=https://tr.alibaba-inc.com/gocFaultDef/innerApi/v2/incident/"
                        "scenarios/level/defs duration_ms=180 result=401/UNAUTHORIZED/RPC_ERROR; "
                        "top=client=security-fourier server=(jedis@r-8vbhsxsii2vswr9bj2.redis."
                        "zhangbei.rds.aliyuncs.com:6379) service=PIPELINESYNC:"
                        "r-8vbhsxsii2vswr9bj2.redis.zhangbei.rds.aliyuncs.com:6379 result=00/OK"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "pattern_auth_session_failure"
    assert "goc-pass" in bundle.hypotheses[0].label
    assert "gocFaultDef" in bundle.hypotheses[0].label
    assert bundle.hypotheses[0].root_layer == "service_dependency"


def test_custom_monitor_signal_keeps_direct_metric_support() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "test",
                "type": "自定义监控",
                "data_ref": "snap",
            },
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
                    "name": "monitor_fields_1026_spm_19",
                    "summary": "custom_monitor code=1026_spm_19 metrics=失败数,成功率 dimensions=代理名",
                },
                {
                    "name": "metric_custom_1026_spm_19_失败数",
                    "command": (
                        "sf metric query 'sum(1026_SPM_19$失败数{代理名=\"gocBlockout\"}) by (代理名)'"
                    ),
                    "returncode": 0,
                    "summary": "[代理名=gocBlockout] count=61 max=66 avg=3.4 trend=rising",
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "custom_monitor_signal"
    assert top.root_layer == "application"
    assert "metric" in top.modalities
    assert [item.name for item in top.support[:2]] == [
        "metric_custom_1026_spm_19_失败数",
        "monitor_fields_1026_spm_19",
    ]


def test_custom_monitor_signal_yields_to_direct_sql_app_error() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "validation",
                "type": "自定义监控",
                "data_ref": "snap",
            },
            "root_candidates": [
                {
                    "kind": "custom_monitor_signal",
                    "label": "1026_SPM_996:失败数:服务=goc.goc-robot.dingGroup.listDingGroupRules.tr",
                    "score": 4.8,
                    "reason": "custom monitor metric max=2 trend=rising",
                },
                {
                    "kind": "app_sql_error",
                    "label": "data_quality:collation_mismatch",
                    "score": 4.1,
                    "reason": "application log shows SQL Illegal mix of collations near alarm window",
                    "props": {
                        "exceptions": ["java.sql.SQLException"],
                        "business_tags": ["IMPLICIT"],
                    },
                },
            ],
            "evidence": [
                {
                    "name": "metric_custom_1026_SPM_996_失败数",
                    "summary": "metric=custom_1026_SPM_996_失败数 series_count=1 top=[服务=x max=2]",
                },
                {
                    "name": "sls_app_application_log_exception",
                    "summary": (
                        'app_logs count=12 top_signals=["kind=app_sql_error '
                        "label=data_quality:collation_mismatch exceptions=['java.sql.SQLException'] "
                        "business_tags=['HY000','IMPLICIT','COERCIBLE']\"]"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "app_sql_error"
    assert bundle.hypotheses[0].label == "data_quality:collation_mismatch"


def test_custom_monitor_offline_change_yields_to_direct_sql_evidence() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "validation",
                "type": "自定义监控",
                "data_ref": "snap",
            },
            "root_candidates": [
                {
                    "kind": "pattern_downstream_offline_change",
                    "label": "corpus-label change_id=3052261299 offline_capacity_change",
                    "score": 8.8,
                    "reason": "broad changefree offline event near alarm",
                },
                {
                    "kind": "evidence_sql",
                    "label": "corpus_label_result_log",
                    "score": 7.8,
                    "reason": "trace and RDS detail show SQL scans dominate latency",
                },
            ],
            "evidence": [
                {
                    "name": "event_changefree_query",
                    "summary": "events count=50 top=change_app=corpus-label offline detail",
                },
                {
                    "name": "rds_sql_slow_detail_rm-abc_633b6c67",
                    "summary": (
                        "rds_sql count=1 top=['kind=detail table=corpus_label_result_log "
                        "cost=1386934 lock_wait=702']"
                    ),
                },
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "evidence_sql"
    assert bundle.hypotheses[0].label == "corpus_label_result_log"


def test_custom_monitor_trace_sql_creates_database_evidence_candidate() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {
                "case_id": "case-1",
                "split": "validation",
                "type": "自定义监控",
                "data_ref": "snap",
            },
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "(tair@:mcomm)",
                    "score": 4.0,
                    "reason": "abnormal trace span",
                    "props": {
                        "service": "INVALID::mcomm:5176",
                        "result_code": "-3998",
                        "evidence_count": 2,
                    },
                },
                {
                    "kind": "trace_span",
                    "label": "(db@cainiao_ae_linehaul_wms)",
                    "score": 3.8,
                    "reason": "abnormal trace span",
                    "props": {
                        "service": "TDDL_QUERY@cainiao_ae_linehaul_wms:linehaul_inbound_abnormal_record",
                        "result_code": "00",
                        "duration_ms": 3381,
                        "evidence_count": 2,
                    },
                },
            ],
            "evidence": [
                {
                    "name": "trace_get_215046e61781",
                    "command": "sf trace get 215046e617812731441602541e0cda -f json",
                    "returncode": 0,
                    "summary": (
                        "trace spans=33 error_top=service=INVALID::mcomm:5176 result=-3998 "
                        "sql_tables={'linehaul_inbound_abnormal_record': 2} "
                        "sql_top=client=ae-linehaul-wms server=(db@cainiao_ae_linehaul_wms) "
                        "service=TDDL_QUERY@cainiao_ae_linehaul_wms:linehaul_inbound_abnormal_record "
                        "duration_ms=3381 result=00/OK"
                    ),
                }
            ],
        }
    )

    assert bundle.hypotheses[0].kind == "evidence_sql"
    assert bundle.hypotheses[0].label == "linehaul_inbound_abnormal_record"


def test_custom_monitor_security_chain_outranks_hsf_metric_and_config_noise() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "validation", "type": "自定义监控"},
            "root_candidates": [
                {
                    "kind": "hsf_service_method",
                    "label": (
                        "tmc-datacubehost->mtop:"
                        "com.alibaba.starseller.reach.QnMobilePopService:1.0.0#get~Q"
                    ),
                    "score": 7.2,
                    "reason": "provider error_qps from mtop is rising",
                }
            ],
            "evidence": [
                {
                    "name": "alarm_get",
                    "command": "sf alarm get abc -f json",
                    "returncode": 0,
                    "summary": (
                        "alarm app=tmc-datacube metric=19_generalComp_189 "
                        "provider success rate dropped for get~Q from mtop"
                    ),
                },
                {
                    "name": "metric_middleware_hsf_provider_service_method_error_qps",
                    "command": "sf metric query middleware_hsf_provider_service_method_error_qps -f json",
                    "returncode": 0,
                    "summary": (
                        "metric=middleware_hsf_provider_service_method_error_qps "
                        "top=[app_group=tmc-datacubehost,remote_app_name=mtop,"
                        "service=com.alibaba.starseller.reach.QnMobilePopService:1.0.0,"
                        "method=get~Q max=0.7 trend=rising]"
                    ),
                },
                {
                    "name": "event_changefree_query",
                    "command": "sf event query -f json",
                    "returncode": 0,
                    "summary": (
                        "events count=1 change_system=aone change_type=CONFIG_PUSH "
                        "change_app=recommend-pro-max dataId=result.notice.config "
                        "crIds=34475993"
                    ),
                },
                {
                    "name": "trace_get_215045261780",
                    "command": "sf trace get 2150452617804690809992404e1231 -f json",
                    "returncode": 0,
                    "summary": (
                        "trace spans=13 client=mtop:mtophost server=tmc-datacube:tmc-datacubehost "
                        "service=QnMobilePopService@get~Q result=01/ERR/BIZ_ERROR; "
                        "client=security-fourier:security-fourierhost service=FOURIER_CHECK_GRPC "
                        "user_data=bx-x5action=break; "
                        "java.lang.RuntimeException caused by com.alibaba.fastjson.JSONException "
                        "and java.lang.SecurityException: RASP has block a real attack"
                    ),
                },
                {
                    "name": "trace_get_noise",
                    "command": "sf trace get noise -f json",
                    "returncode": 0,
                    "summary": (
                        "trace top=service=MQRecv@CARGO_FULL_LINK_CHECK_TASK_HIGH_PRIORITY_TOPIC "
                        "result=1/BIZ_ERROR"
                    ),
                },
            ],
        }
    )

    top = bundle.hypotheses[0]

    assert top.kind == "pattern_security_scan"
    assert top.label == "mtop security_scan"
    assert top.root_layer == "security"
