from __future__ import annotations

from collections import Counter

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.enrichment import (
    TrajectoryTerm,
    enrich_answer,
    terms_from_audit_case,
)
from tests.benchmarks.realrca_graph.models import CandidateAnswer


def _bundle():
    return build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "ontology": ["Case", "Trace", "MetricSeries", "LogError"],
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app:provider_group",
                    "score": 5.0,
                    "reason": "provider-app ProviderApi timeout and HSF-0001",
                    "props": {
                        "service": "com.alibaba.demo.ProviderApi:1.0.0@getThing~P",
                        "trace_id": "212a6a3417840231458777961e0d45",
                    },
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                    "returncode": 0,
                    "summary": (
                        "provider-app com.alibaba.demo.ProviderApi@getThing failed "
                        "with HSF-0001 and HSFServiceAddressNotFoundException"
                    ),
                },
                {
                    "name": "metric_provider_rt",
                    "command": "sf metric query hsf_provider_rt -f json",
                    "returncode": 0,
                    "summary": "provider-app RT rose in the alarm window",
                },
            ],
        }
    )


def test_enrich_answer_appends_root_aligned_graph_supported_term() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app 的 com.alibaba.demo.ProviderApi@getThing 调用失败导致成功率下降。",
        "212a6a3417840231458777961e0d45",
    )
    term = TrajectoryTerm(
        term="HSFServiceAddressNotFoundException",
        kind="exception",
        score=24,
        count=3,
        graph_supported=True,
        event_counts=Counter({"agent.tool_result": 3}),
        snippets=["provider-app ProviderApi failed with HSFServiceAddressNotFoundException"],
    )

    decision = enrich_answer(baseline, _bundle(), [term])

    assert decision.changed is True
    assert decision.candidate.trace_id == baseline.trace_id
    assert "HSFServiceAddressNotFoundException" in decision.candidate.diagnosis_output
    assert decision.score.baseline_retention == 1.0


def test_enrich_answer_rejects_unaligned_or_nongraph_terms() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app 的 com.alibaba.demo.ProviderApi@getThing 调用超时导致成功率下降。",
        "212a6a3417840231458777961e0d45",
    )
    terms = [
        TrajectoryTerm(
            term="AttributeError",
            kind="exception",
            score=28,
            graph_supported=True,
            event_counts=Counter({"agent.tool_result": 1}),
            snippets=["python script AttributeError while parsing"],
        ),
        TrajectoryTerm(
            term="rm-8vb2es51fz7j280sz",
            kind="rds",
            score=28,
            graph_supported=False,
            event_counts=Counter({"agent.tool_result": 8}),
            snippets=["unrelated database rm-8vb2es51fz7j280sz"],
        ),
    ]

    decision = enrich_answer(baseline, _bundle(), terms)

    assert decision.changed is False
    assert decision.candidate == baseline
    reasons = {item["reason"] for item in decision.rejected_terms}
    assert "generic process or framework exception" in reasons
    assert (
        "term is not present in graph evidence" in reasons
        or "term conflicts with baseline failure mechanism" in reasons
    )


def test_enrich_answer_rejects_app_only_exception_for_sql_root() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：panpipe-web 对 panpipe 库 notice_close 表执行 TDDL_QUERY 全表扫描导致读 RT 升高。",
        "21055a3017852879485546053e11be",
    )
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "TDDL", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "evidence_cluster",
                    "label": "ip:33.7.25.81",
                    "score": 4.8,
                    "reason": "panpipe-web log error and metric signal",
                    "props": {
                        "top_signals": [
                            {
                                "kind": "log_error",
                                "label": "com.aliyun.tea.TeaException",
                                "reason": "concrete error log near alarm",
                            }
                        ]
                    },
                }
            ],
            "evidence": [
                {
                    "name": "log_error_list",
                    "command": "sf log error list --app panpipe-web -f json",
                    "returncode": 0,
                    "summary": "panpipe-web DingApiServiceImpl com.aliyun.tea.TeaException cardNotExist",
                }
            ],
        }
    )
    term = TrajectoryTerm(
        term="com.aliyun.tea.TeaException",
        kind="exception",
        score=24,
        graph_supported=True,
        event_counts=Counter({"agent.tool_result": 4}),
        snippets=["panpipe-web DingApiServiceImpl com.aliyun.tea.TeaException cardNotExist"],
    )

    decision = enrich_answer(baseline, bundle, [term])

    assert decision.changed is False
    assert decision.rejected_terms[0]["reason"] in {
        "term kind conflicts with case/root domain",
        "term conflicts with baseline failure mechanism",
    }


def test_enrich_answer_deduplicates_full_and_simple_exception_names() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app 的 com.alibaba.demo.ProviderApi@getThing 调用超时导致成功率下降。",
        "212a6a3417840231458777961e0d45",
    )
    terms = [
        TrajectoryTerm(
            term="com.alibaba.demo.ProviderException",
            kind="exception",
            score=24,
            graph_supported=True,
            event_counts=Counter({"agent.tool_result": 2}),
            snippets=["ProviderApi failed with com.alibaba.demo.ProviderException"],
        ),
        TrajectoryTerm(
            term="ProviderException",
            kind="exception",
            score=23,
            graph_supported=True,
            event_counts=Counter({"agent.tool_result": 2}),
            snippets=["ProviderApi failed with ProviderException"],
        ),
    ]
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snap"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app",
                    "score": 5.0,
                    "reason": "ProviderApi failed with com.alibaba.demo.ProviderException",
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                    "summary": "ProviderApi failed with com.alibaba.demo.ProviderException",
                }
            ],
        }
    )

    decision = enrich_answer(baseline, bundle, terms)

    assert decision.changed is True
    assert [term.term for term in decision.selected_terms] == ["com.alibaba.demo.ProviderException"]


def test_enrich_answer_rejects_generic_wrapper_exception() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：adstar-mkt-campaign 调用 union-seller-cpa 的 HSF 请求超时。",
        "214782ea17841106271425111e0a19",
    )
    term = TrajectoryTerm(
        term="com.taobao.union.common.TkException",
        kind="exception",
        score=24,
        graph_supported=True,
        event_counts=Counter({"agent.tool_result": 4}),
        snippets=["adstar-mkt-campaign OrderQueryServiceImpl com.taobao.union.common.TkException"],
    )

    decision = enrich_answer(baseline, _bundle(), [term])

    assert decision.changed is False
    assert decision.rejected_terms[0]["reason"] == "generic wrapper exception"


def test_enrich_answer_rejects_semantically_covered_timeout_term() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app 的 HSF 调用在 3000ms 返回 rc=03 超时，导致成功率下降。",
        "212a6a3417840231458777961e0d45",
    )
    term = TrajectoryTerm(
        term="HSFTimeOutException",
        kind="exception",
        score=24,
        graph_supported=True,
        event_counts=Counter({"agent.tool_result": 4}),
        snippets=["ProviderApi failed with HSFTimeOutException"],
    )

    decision = enrich_answer(baseline, _bundle(), [term])

    assert decision.changed is False
    assert decision.rejected_terms[0]["reason"] == "already covered by baseline answer"


def test_enrich_answer_rejects_term_from_different_failure_mechanism() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app 个别实例发生 JVM FullGC/Stop-The-World，导致调用超时。",
        "212a6a3417840231458777961e0d45",
    )
    term = TrajectoryTerm(
        term="com.alibaba.fastjson.JSONException",
        kind="exception",
        score=24,
        graph_supported=True,
        event_counts=Counter({"agent.tool_result": 4}),
        snippets=["provider-app query failed with com.alibaba.fastjson.JSONException"],
    )

    decision = enrich_answer(baseline, _bundle(), [term])

    assert decision.changed is False
    assert decision.rejected_terms[0]["reason"] == "term conflicts with baseline failure mechanism"


def test_enrich_answer_accepts_cache_miss_for_cache_mechanism() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app 的 com.alibaba.demo.ProviderApi@getThing Tair 缓存未预热，导致请求回源并超时。",
        "212a6a3417840231458777961e0d45",
    )
    term = TrajectoryTerm(
        term="DATANOTEXSITS",
        kind="strong_phrase",
        score=24,
        graph_supported=True,
        event_counts=Counter({"agent.tool_result": 4}),
        snippets=["provider-app ProviderApi Tair GET returned DATANOTEXSITS"],
    )

    decision = enrich_answer(baseline, _bundle(), [term])

    assert decision.changed is True
    assert "DATANOTEXSITS" in decision.candidate.diagnosis_output


def test_terms_from_audit_case_parses_old_audit_shape() -> None:
    terms = terms_from_audit_case(
        {
            "missing_terms": [
                {
                    "term": "HSF-0001",
                    "kind": "hsf_code",
                    "score": 21,
                    "count": 4,
                    "graph_supported": True,
                    "event_counts": {"agent.tool_result": 4},
                    "occurrences": [{"snippet": "provider-app returned HSF-0001"}],
                }
            ]
        }
    )

    assert terms[0].term == "HSF-0001"
    assert terms[0].event_counts["agent.tool_result"] == 4
    assert terms[0].snippets == ["provider-app returned HSF-0001"]
