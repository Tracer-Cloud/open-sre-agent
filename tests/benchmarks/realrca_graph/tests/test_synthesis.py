from __future__ import annotations

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.synthesis import synthesize_answer


def test_synthesize_answer_uses_non_contradicted_multimodal_hypothesis() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "ontology": ["Case", "Trace", "MetricSeries"],
            "retrieval_summary": "",
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app:group",
                    "score": 5.0,
                    "reason": "provider timeout",
                    "props": {
                        "trace_id": "212a6a3417840231458777961e0d45",
                        "service": "com.alibaba.demo.ProviderApi:1.0.0@getThing~P",
                    },
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
                    "name": "metric_middleware_hsf_provider_service_method_rt",
                    "command": "sf metric query middleware_hsf_provider_service_method_rt -f json",
                    "returncode": 0,
                    "summary": "provider-app RT rose during the alarm window",
                },
            ],
        }
    )

    answer = synthesize_answer(bundle)

    assert answer.case_id == "case-1"
    assert "provider-app:group" in answer.diagnosis_output
    assert answer.trace_id == "212a6a3417840231458777961e0d45"
    assert "图谱" not in answer.diagnosis_output
    assert "evidence bundle" not in answer.diagnosis_output
    assert "candidate" not in answer.diagnosis_output.lower()
    assert "关键证据" in answer.diagnosis_output
    assert "处置建议" in answer.diagnosis_output


def test_synthesize_answer_adds_sql_write_rt_anchor() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-2", "split": "validation", "type": "TDDL"},
            "ontology": ["Case", "MetricSeries"],
            "retrieval_summary": "",
            "root_candidates": [
                {
                    "kind": "evidence_sql",
                    "label": "demo_order_table",
                    "score": 8.0,
                    "reason": "TDDL write_table_rt spike",
                    "props": {"sql_table": "demo_order_table"},
                },
            ],
            "evidence": [
                {
                    "name": "metric_middleware_tddl_write_table_rt",
                    "command": "sf metric query middleware_tddl_write_table_rt",
                    "summary": "table=demo_order_table max=1200 trend=rising",
                },
            ],
        }
    )

    answer = synthesize_answer(bundle)

    assert "写RT异常表定位" in answer.diagnosis_output


def test_synthesize_answer_adds_mq_cpu_anchor() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-4", "split": "validation", "type": "CPU"},
            "ontology": ["Case", "MetricSeries"],
            "retrieval_summary": "",
            "root_candidates": [
                {
                    "kind": "pattern_mq_spike",
                    "label": "demo-consumer",
                    "score": 8.0,
                    "reason": "visible MetaQ metric series indicates message volume spike",
                },
            ],
            "evidence": [
                {
                    "name": "metric_middleware_metaq_clnt_receive_group_id_qps",
                    "summary": "group_id=demo-consumer max=10000 trend=rising",
                },
            ],
        }
    )

    answer = synthesize_answer(bundle)

    assert "MQ消费激增致CPU打高" in answer.diagnosis_output


def test_synthesize_answer_adds_jvm_gc_pressure_without_full_gc_anchor() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-5", "split": "validation", "type": "自定义监控"},
            "ontology": ["Case", "MetricSeries"],
            "retrieval_summary": "",
            "root_candidates": [
                {
                    "kind": "pattern_jvm_gc_pressure",
                    "label": "33.42.120.77",
                    "score": 7.8,
                    "reason": "visible JVM GC metric text indicates GC pause-time pressure",
                    "props": {"gc_pressure": True},
                },
            ],
            "evidence": [
                {
                    "name": "metric_jvm_gc_time_delta",
                    "summary": (
                        "metric=jvm_gc_time_delta "
                        "ip=33.42.120.77 gc=g1_young_generation max=481 trend=rising"
                    ),
                },
            ],
        }
    )

    answer = synthesize_answer(bundle)

    assert "JVM GC压力" in answer.diagnosis_output
    assert "Full GC" not in answer.diagnosis_output


def test_synthesize_answer_mentions_complementary_cache_signal() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-3", "split": "validation", "type": "HSF"},
            "ontology": ["Case", "Trace", "MetricSeries"],
            "retrieval_summary": "",
            "root_candidates": [
                {
                    "kind": "pattern_limit",
                    "label": "demo.DetailService@get",
                    "score": 9.0,
                    "reason": "visible log/trace text indicates Sentinel runtime limiting",
                },
                {
                    "kind": "pattern_cache_timeout",
                    "label": "r-8vbb47dd6e120014",
                    "score": 7.2,
                    "reason": "visible Redis/Tair evidence indicates cache timeout",
                },
            ],
            "evidence": [
                {
                    "name": "log_error_list",
                    "summary": "SentinelBlockException flow control on DetailService",
                },
                {
                    "name": "trace_get_cache",
                    "summary": "jedis@r-8vbb47dd6e120014.redis timeout and cache hit drop",
                },
            ],
        }
    )

    answer = synthesize_answer(bundle)

    assert "接口限流" in answer.diagnosis_output
    assert "缓存命中率下降" in answer.diagnosis_output
    assert "r-8vbb47dd6e120014 表" not in answer.diagnosis_output


def test_synthesize_answer_keeps_soft_hsf_service_boundary() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-5", "split": "validation", "type": "HSF"},
            "ontology": ["Case", "MetricSeries"],
            "retrieval_summary": "",
            "root_candidates": [
                {
                    "kind": "hsf_service_method",
                    "label": (
                        "consumer-host:com.aliexpress.sellingpoint.api.service."
                        "SellingPointQueryFacadeV2:1.0.0#querySellingPointsByBizIds~B"
                    ),
                    "score": 8.0,
                    "reason": (
                        "HSF metric labels from metric_middleware_hsf_consumer_service_method_error_qps"
                    ),
                },
                {
                    "kind": "metric_series",
                    "label": "middleware_hsf_consumer_service_method_rt:consumer-host",
                    "score": 7.0,
                    "reason": "metric series near alarm window",
                },
            ],
            "evidence": [
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "summary": "hsf消费者接口异常qps max=147 trend=rising",
                },
                {
                    "name": "metric_middleware_hsf_consumer_service_method_rt",
                    "summary": "consumer method rt max=1317 trend=rising",
                },
            ],
        }
    )

    answer = synthesize_answer(bundle)

    assert "SellingPointQueryFacadeV2" in answer.diagnosis_output
    assert "下游线程池打满定位" in answer.diagnosis_output


def test_synthesize_answer_distinguishes_external_dependency_timeout() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-6", "split": "validation", "type": "HSF"},
            "ontology": ["Case", "Log"],
            "retrieval_summary": "",
            "root_candidates": [
                {
                    "kind": "pattern_external_dependency",
                    "label": "developer.ehuandian.net",
                    "score": 8.3,
                    "reason": (
                        "visible trace/log text indicates downstream external dependency "
                        "timeout or unreachable connection failure"
                    ),
                },
                {
                    "kind": "pattern_threadpool_busy",
                    "label": "threadpool_busy",
                    "score": 8.0,
                    "reason": "org.eclipse.jetty.util.thread.QueuedThreadPool.runJob",
                },
            ],
            "evidence": [
                {
                    "name": "sls_app_logs",
                    "summary": (
                        "java.net.NoRouteToHostException developer.ehuandian.net "
                        "logger=org.eclipse.jetty.util.thread.QueuedThreadPool"
                    ),
                },
            ],
        }
    )

    answer = synthesize_answer(bundle)

    assert "developer.ehuandian.net" in answer.diagnosis_output
    assert "下游服务超时" in answer.diagnosis_output
    assert "下游线程池打满定位" not in answer.diagnosis_output
