from __future__ import annotations

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.models import (
    CandidateAnswer,
    EvidenceBundle,
    EvidenceItem,
    RootHypothesis,
)
from tests.benchmarks.realrca_graph.probe_feedback import ProbeFeedbackLedger
from tests.benchmarks.realrca_graph.verifier import decide_candidate, score_candidate


def _bundle():
    return build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF", "data_ref": "snapshot-1"},
            "ontology": ["Case", "Alarm", "Service", "Trace", "MetricSeries", "LogError"],
            "retrieval_summary": "provider-app is upstream of consumer-app",
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app:provider_group",
                    "score": 5.0,
                    "reason": "upstream provider timeout",
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
                    "name": "trace_get",
                    "command": "sf trace get 212a6a3417840231458777961e0d45 -f json",
                    "returncode": 0,
                    "summary": "provider-app com.alibaba.demo.ProviderApi@getThing timeout at 10000ms",
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
        }
    )


def test_verifier_accepts_better_supported_multimodal_candidate() -> None:
    baseline = CandidateAnswer(
        "baseline", "case-1", "consumer-app success rate dropped.", "fallback"
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "Root cause: provider-app com.alibaba.demo.ProviderApi@getThing timed out. "
            "Trace 212a6a3417840231458777961e0d45 shows provider duration 10000ms, "
            "provider RT metric rose sharply, and HSFTimeOutException appears in logs. "
            "consumer-app is the downstream symptom."
        ),
        "212a6a3417840231458777961e0d45",
    )

    decision = decide_candidate(
        baseline,
        [candidate],
        _bundle(),
        min_support=0.45,
        min_margin=0.05,
        max_novelty=1.0,
    )

    assert decision.accepted_replacement is True
    assert decision.selected.source == "candidate"


def test_verifier_rejects_unsupported_novel_candidate() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app ProviderApi timeout caused consumer success rate drop.",
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "Root cause: unrelated payment database rm-deadbeef slow SQL caused the outage.",
        "other",
    )

    decision = decide_candidate(baseline, [candidate], _bundle(), min_support=0.45, min_margin=0.05)

    assert decision.accepted_replacement is False
    assert decision.selected.source == "baseline"


def test_verifier_keeps_stable_supported_baseline_on_small_support_gain() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "Root cause: provider-app com.alibaba.demo.ProviderApi@getThing timed out. "
            "Trace and provider RT metric both point to provider-app."
        ),
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "Root cause: provider-app com.alibaba.demo.ProviderApi@getThing timed out. "
            "Trace 212a6a3417840231458777961e0d45 shows provider duration 10000ms, "
            "provider RT metric rose sharply, and HSFTimeOutException appears in logs. "
            "consumer-app is the downstream symptom."
        ),
        "212a6a3417840231458777961e0d45",
    )

    decision = decide_candidate(
        baseline,
        [candidate],
        _bundle(),
        min_support=0.45,
        min_margin=0.05,
        max_novelty=1.0,
    )

    assert decision.accepted_replacement is False
    assert decision.selected.source == "baseline"


def test_score_prefers_clean_hypothesis_when_overlap_ties() -> None:
    evidence = [
        EvidenceItem(
            id="e1",
            name="trace_get",
            modality="trace",
            summary="provider-app ProviderApi timeout",
            score=1.35,
        ),
        EvidenceItem(
            id="e2",
            name="metric_provider_rt",
            modality="metric",
            summary="provider-app RT rose sharply",
            score=1.35,
        ),
    ]
    bundle = EvidenceBundle(
        case_id="case-1",
        split="test",
        case_type="HSF",
        data_ref="snapshot",
        ontology=[],
        retrieval_summary="",
        evidence=evidence,
        hypotheses=[
            RootHypothesis(
                id="h1",
                kind="ip",
                label="provider-app",
                root_layer="infrastructure",
                score=9.0,
                reason="generic provider-app entity",
                modalities=["trace", "metric"],
                support=evidence,
                contradictions=["generic entity is ambiguous"],
            ),
            RootHypothesis(
                id="h2",
                kind="trace_span",
                label="provider-app",
                root_layer="service_dependency",
                score=5.0,
                reason="provider-app ProviderApi timeout",
                modalities=["trace", "metric"],
                support=evidence,
                contradictions=[],
            ),
        ],
    )
    answer = CandidateAnswer(
        "candidate",
        "case-1",
        "provider-app ProviderApi timeout caused the HSF success-rate drop.",
        "trace",
    )

    score = score_candidate(answer, answer, bundle)

    assert score.best_hypothesis_id == "h2"
    assert "best_hypothesis_has_contradiction" not in score.risk_flags


def test_score_does_not_treat_excluded_alternative_as_positive_root() -> None:
    evidence = [
        EvidenceItem(
            id="e1",
            name="log_threadpool_busy",
            modality="log",
            summary="THREADPOOL_BUSY at provider-app 33.1.203.42",
            score=1.35,
        ),
        EvidenceItem(
            id="e2",
            name="trace_get",
            modality="trace",
            summary="provider-app queryNationSpace returned RPC_ERROR",
            score=1.35,
        ),
    ]
    bundle = EvidenceBundle(
        case_id="case-1",
        split="test",
        case_type="HSF",
        data_ref="snapshot",
        ontology=[],
        retrieval_summary="",
        evidence=evidence,
        hypotheses=[
            RootHypothesis(
                id="h-cache",
                kind="pattern_cache_timeout",
                label="cache_timeout",
                root_layer="cache",
                score=5.8,
                reason="cache timeout appears in a trace",
                modalities=["trace", "log"],
                support=evidence,
            ),
            RootHypothesis(
                id="h-threadpool",
                kind="hsf_threadpool_busy",
                label="THREADPOOL_BUSY:33.1.203.42",
                root_layer="service_dependency",
                score=5.7,
                reason="Provider HSF thread pool is full",
                modalities=["trace", "log"],
                support=evidence,
            ),
        ],
    )
    answer = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因定位：provider-app 33.1.203.42 出现 HSF Provider 线程池耗尽，返回 "
            "THREADPOOL_BUSY。关键证据：日志显示 THREADPOOL_BUSY，Trace 显示 queryNationSpace "
            "返回 RPC_ERROR。影响链路：线程池满导致新请求被拒绝并触发告警。排除项：不将 "
            "cache_timeout 作为主因。处置建议：摘除实例并排查阻塞线程。"
        ),
        "2103052617864108333715270e0f89",
    )

    score = score_candidate(answer, answer, bundle)

    assert score.best_hypothesis_id == "h-threadpool"


def test_score_ignores_unrelated_cause_that_cannot_explain_symptom() -> None:
    evidence = [
        EvidenceItem(
            id="e1",
            name="trace_get",
            modality="trace",
            summary="Tair ldbicbu returned a soft error in a sampled trace",
            score=1.35,
        )
    ]
    bundle = EvidenceBundle(
        case_id="case-1",
        split="test",
        case_type="机器存活数",
        data_ref="snapshot",
        ontology=[],
        retrieval_summary="",
        evidence=evidence,
        hypotheses=[
            RootHypothesis(
                id="h-tair",
                kind="pattern_cache_timeout",
                label="tair@2dbea1497c924275:ldbicbu",
                root_layer="cache",
                score=8.0,
                reason="sampled Tair soft error",
                modalities=["trace"],
                support=evidence,
            )
        ],
    )
    answer = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因定位为 mtee3 执行 Normandy Director 主动 Pod 驱逐/缩容，导致存活实例数"
            "实际减少并触发告警。采样 Trace 仅见正常调用及少量无关 Tair 软错误，不能解释"
            "机器数下降。"
        ),
        "codex-59273e99fdb04256915fe11981d5665d",
    )

    score = score_candidate(answer, answer, bundle)

    assert score.best_hypothesis_id == ""
    assert "no_hypothesis_overlap" in score.risk_flags


def test_score_prefers_root_focus_over_support_overlap() -> None:
    evidence = [
        EvidenceItem(
            id="e1",
            name="metric_middleware_metaq_clnt_receive_group_id_qps",
            modality="metric",
            summary=(
                "topic=ae_gbrain_item_real_time_rebuild max=4004 trend=rising; "
                "host 11.175.207.156 cpu reached 98%"
            ),
            score=1.35,
        ),
        EvidenceItem(
            id="e2",
            name="metric_pod_cpu_limit_usage",
            modality="metric",
            summary="host 11.175.207.156 cpu reached 98%",
            score=1.35,
        ),
    ]
    bundle = EvidenceBundle(
        case_id="case-1",
        split="test",
        case_type="CPU",
        data_ref="snapshot",
        ontology=[],
        retrieval_summary="",
        evidence=evidence,
        hypotheses=[
            RootHypothesis(
                id="h-host",
                kind="pattern_host_anomaly",
                label="11.175.207.156",
                root_layer="infrastructure",
                score=8.0,
                reason="host CPU saturation",
                modalities=["metric"],
                support=evidence,
            ),
            RootHypothesis(
                id="h-mq",
                kind="pattern_mq_spike",
                label="ae_gbrain_item_real_time_rebuild",
                root_layer="message_queue",
                score=7.5,
                reason="MetaQ receive topic trend=rising",
                modalities=["metric"],
                support=[evidence[0]],
            ),
        ],
    )
    answer = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因是 METAQ topic ae_gbrain_item_real_time_rebuild 集中消费，触发 CPU "
            "密集型索引重建。关键证据：11.175.207.156 CPU 达到 98%。"
        ),
        "0a0ddfec17845309800471313d0001",
    )

    score = score_candidate(answer, answer, bundle)

    assert score.best_hypothesis_id == "h-mq"


def test_candidate_with_synthetic_trace_id_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app ProviderApi timeout caused consumer success rate drop.",
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "provider-app ProviderApi timeout caused consumer success rate drop.",
        "dma-cce321ef",
    )

    score = score_candidate(candidate, baseline, _bundle())

    assert "synthetic_or_invalid_trace_id" in score.risk_flags


def test_candidate_preserving_baseline_synthetic_trace_id_is_not_new_trace_risk() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app ProviderApi timeout caused consumer success rate drop.",
        "codex-cce321ef",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "provider-app ProviderApi timeout caused consumer success rate drop with HSF timeout evidence.",
        "codex-cce321ef",
    )

    score = score_candidate(candidate, baseline, _bundle())

    assert "synthetic_or_invalid_trace_id" not in score.risk_flags


def test_candidate_with_evaluation_leakage_terms_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app ProviderApi timeout caused consumer success rate drop.",
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "validation 案例显示同类问题，provider-app ProviderApi timeout caused the drop.",
        "212a6a3417840231458777961e0d45",
    )

    score = score_candidate(candidate, baseline, _bundle())

    assert "evaluation_or_experiment_leakage_terms" in score.risk_flags


def test_candidate_with_graph_process_terms_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app ProviderApi timeout caused consumer success rate drop.",
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "图谱 top_root_candidates 中 provider-app 得分最高，trace_list 证明它是根因。",
        "212a6a3417840231458777961e0d45",
    )

    score = score_candidate(candidate, baseline, _bundle())

    assert "evaluation_or_experiment_leakage_terms" in score.risk_flags


def test_candidate_with_evidence_bundle_process_terms_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app HSF 超时。",
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "根因：provider-app HSF 超时；证据包中 h1 假设支持该结论。",
        "212a6a3417840231458777961e0d45",
    )

    score = score_candidate(candidate, baseline, _bundle())

    assert "evaluation_or_experiment_leakage_terms" in score.risk_flags


def test_candidate_that_only_expands_supported_baseline_evidence_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app ProviderApi timeout caused consumer-app HSF errors.",
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "provider-app ProviderApi timeout caused consumer-app HSF errors. "
            "Trace shows 10000ms timeout, metric shows provider RT rose, and logs show HSFTimeOutException."
        ),
        "212a6a3417840231458777961e0d45",
    )

    score = score_candidate(candidate, baseline, _bundle())

    assert "likely_evidence_only_expansion" in score.risk_flags


def test_candidate_that_expands_same_app_host_root_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "根因定位为下游 union-seller-cpa 单机 33.6.249.194 故障，"
            "OneDeliveryProjectReadService.query 请求超时，导致上游 HSF 异常。"
        ),
        "214782ea17841106271425111e0a19",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因定位为下游 union-seller-cpa 单机 33.6.249.194 故障，"
            "OneDeliveryProjectReadService.query 请求超时，导致上游 HSF 异常。"
            "补充证据显示同一实例上的 KoxListReadService.queryKoxCountInEventByStatus "
            "也出现 RPC_ERROR，说明异常并非单个业务方法，而是该实例服务能力不稳定。"
            "Trace 指向该主机，指标也显示成功率下降和 RT 上升。"
        ),
        "214782ea17841106271425111e0a19",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert score.baseline_retention == 1.0
    assert "same_root_evidence_expansion" in score.risk_flags


def test_candidate_that_compresses_away_baseline_details_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "根因：consumer-app 的 na610_host、na620_host 在 05:17 同时扩容，"
            "新实例 33.44.208.253、33.44.209.163、33.44.209.223 尚未预热即接流，"
            "ProviderApi query RT 从 2.3ms 升至 19.5ms，导致 tp90 告警。"
        ),
        "codex-cce321ef",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "根因：consumer-app 扩容后新实例未预热即接流，ProviderApi query RT 升高触发告警。",
        "codex-cce321ef",
    )

    score = score_candidate(candidate, baseline, _bundle())

    assert "lossy_baseline_compression" in score.risk_flags


def test_candidate_that_rewrites_and_drops_baseline_context_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "根因：consumer-app 的 na610、na620 两个分组在告警前同时扩容，"
            "新实例未预热导致 com.demo.ProviderApi:query tp90 升高；变更窗口和指标窗口吻合。"
        ),
        "codex-cce321ef",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因：consumer-app 扩容后冷启动导致 com.demo.ProviderApi:query 调用变慢。"
            "指标显示 ProviderApi RT 上升，告警指向同一接口；成功率无异常，排除业务报错。"
        ),
        "codex-cce321ef",
    )

    score = score_candidate(candidate, baseline, _bundle())

    assert "rewrite_drops_baseline_context" in score.risk_flags


def test_candidate_with_partial_baseline_context_loss_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "根因定位：mp-fund 生产发布版本 234485125 触发 service destroy，"
            "导致 mpf-monitor 记录大量 DP_CREATE|NO_QUALIFICATION。"
        ),
        "21082c4f17848621591888406d08ba",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因定位：mp-fund 生产发布版本 234485125 触发 service destroy，"
            "日志显示 MpfSystemException 和 MpfBizException，导致 DP_CREATE|NO_QUALIFICATION。"
        ),
        "21082c4f17848621591888406d08ba",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert score.baseline_retention < 0.82
    assert "partial_baseline_context_loss" in score.risk_flags


def test_candidate_dropping_baseline_entities_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "provider-app com.alibaba.demo.ProviderApi:getThing "
            "HSFTimeOutException caused consumer-app success rate drop."
        ),
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "payment-app rm-deadbeef slow SQL caused the outage.",
        "212a6a3417840231458777961e0d45",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert score.baseline_retention < 0.45
    assert "drops_baseline_critical_tokens" in score.risk_flags


def test_candidate_adding_secondary_trace_ids_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "根因定位：trade-contract 单机实例 33.1.203.42 出现 HSF Provider "
            "业务线程池耗尽并返回 THREADPOOL_BUSY。Trace "
            "2103052617864108333715270e0f89 显示 NationSpaceService.queryNationSpace 返回 RPC_ERROR。"
        ),
        "2103052617864108333715270e0f89",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因定位：trade-contract 单机实例 33.1.203.42 出现 HSF Provider "
            "业务线程池耗尽并返回 THREADPOOL_BUSY。Trace "
            "2103052617864108333715270e0f89 显示 NationSpaceService.queryNationSpace 返回 RPC_ERROR；"
            "Trace 716080a317864108267631712e 还显示 queryTradeWithoutContractById 变慢。"
        ),
        "2103052617864108333715270e0f89",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert "adds_secondary_trace_ids" in score.risk_flags


def test_candidate_contradicting_baseline_direct_log_evidence_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "根因：CouponPollingMessageListener 调用 processFunderCouponChange 第336行时抛出 "
            "BizException。关键证据：00:45:41 多个 ConsumeMessageThread 并发出现同类异常，"
            "涉及不同 couponCode 和 msgId。"
        ),
        "212c4c8e17862075418048755d0f3a",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因：CouponPollingMessageListener 查询优惠券失败导致消费失败。"
            "不确定性：Trace 和日志检索在告警窗口内未返回有效记录，异常日志和 Trace 链路缺失。"
        ),
        "212c4c8e17862075418048755d0f3a",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert "contradicts_baseline_direct_evidence" in score.risk_flags


def test_candidate_using_baseline_negated_mechanism_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "根因定位：provider-app 单机 Full GC 导致 JVM 长时间停顿，"
            "不是 HSF 线程池打满；线程池使用率只有 18%，排除线程池容量问题。"
        ),
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因定位：provider-app HSF Provider 线程池耗尽并返回 THREADPOOL_BUSY，"
            "导致上游请求超时。关键证据：Trace 显示 provider 超时，指标显示 RT 上涨。"
        ),
        "212a6a3417840231458777961e0d45",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert "uses_baseline_negated_mechanism" in score.risk_flags


def test_baseline_negation_does_not_mark_affirmed_root_as_negated() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "根因定位：provider-app 单机 Full GC 导致 JVM 长时间停顿，"
            "不是 HSF 线程池打满；线程池使用率只有 18%，排除线程池容量问题。"
        ),
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因定位：provider-app 单机 Full GC 导致 JVM 长时间停顿。"
            "关键证据：Trace 显示 provider 长时间停顿，指标显示 RT 上涨。"
        ),
        "212a6a3417840231458777961e0d45",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert "uses_baseline_negated_mechanism" not in score.risk_flags


def test_generic_hsf_timeout_does_not_match_threadpool_negation() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app 调用下游超时；线程池使用率仅 18%，排除线程池容量问题。",
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "根因：provider-app HSF 接口调用下游耗时升高并返回 TIMEOUT，导致成功率下降。",
        "212a6a3417840231458777961e0d45",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert "uses_baseline_negated_mechanism" not in score.risk_flags


def test_entity_scoped_threadpool_negation_does_not_block_different_provider() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：trade-contract 单机 33.1.203.42 Provider 线程池打满；不是 tradelist 自身线程池故障。",
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "根因：trade-contract 单机 33.1.203.42 HSF Provider 线程池耗尽并返回 THREADPOOL_BUSY。",
        "212a6a3417840231458777961e0d45",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert "uses_baseline_negated_mechanism" not in score.risk_flags


def test_traffic_spike_negation_does_not_block_affirmed_threadpool_root() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "根因：下游 fin-cif 单机 33.62.98.154 HSF Provider 线程池使用率突升至100%，"
            "导致 THREADPOOL_BUSY 拒绝及超时；同期总 HSF QPS未上升，排除流量突增打满。"
        ),
        "0b51f53117833290692323772d104b",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "根因：fin-cif 单机 33.62.98.154 HSF Provider 线程池耗尽并返回 THREADPOOL_BUSY。",
        "0b51f53117833290692323772d104b",
    )

    score = score_candidate(candidate, baseline, _bundle(), high_novelty_threshold=1.0)

    assert "uses_baseline_negated_mechanism" not in score.risk_flags


def test_candidate_from_negative_probe_family_is_risky() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "consumer-app success rate dropped.",
        "212a6a3417840231458777961e0d45",
    )
    candidate = CandidateAnswer(
        "results-test-evidence-gen-v3-weak-risky",
        "case-1",
        (
            "Root cause: provider-app com.alibaba.demo.ProviderApi@getThing timed out. "
            "Trace 212a6a3417840231458777961e0d45 shows provider duration 10000ms, "
            "provider RT metric rose sharply, and HSFTimeOutException appears in logs."
        ),
        "212a6a3417840231458777961e0d45",
    )
    ledger = ProbeFeedbackLedger.from_leaderboard(
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
        team_name="隐元玩一玩",
    )

    score = score_candidate(
        candidate,
        baseline,
        _bundle(),
        high_novelty_threshold=1.0,
        probe_feedback=ledger.cases["21f4"],
    )

    assert "negative_leaderboard_probe_family" in score.risk_flags
