from __future__ import annotations

import re

from tests.benchmarks.realrca_graph.answer_anchors import (
    answer_anchor_sentences,
    has_hard_contradiction,
)
from tests.benchmarks.realrca_graph.features import clip_text
from tests.benchmarks.realrca_graph.models import CandidateAnswer, EvidenceBundle, RootHypothesis

PROCESS_TERM_RE = re.compile(
    r"\b(?:visible|graph|evidence bundle|candidate|hypothesis|root_candidates?)\b|"
    r"图谱|证据包|候选",
    re.I,
)


def choose_primary_hypothesis(bundle: EvidenceBundle) -> RootHypothesis | None:
    """Pick the safest graph hypothesis for deterministic answer synthesis."""

    for hypothesis in bundle.hypotheses:
        if not has_hard_contradiction(hypothesis):
            return hypothesis
    return bundle.hypotheses[0] if bundle.hypotheses else None


def _trace_id(hypothesis: RootHypothesis) -> str:
    traces = hypothesis.entities.get("traces") or []
    if traces:
        return traces[0]
    for item in hypothesis.support:
        for token in item.summary.split():
            normalized = token.strip(" ,.;:()[]{}<>\"'`").lower()
            if 12 <= len(normalized) <= 40 and all(
                char in "0123456789abcdef" for char in normalized
            ):
                return normalized
    return f"ontology-{hypothesis.id}"


def synthesize_answer(
    bundle: EvidenceBundle, *, source: str = "ontology-synth-v1"
) -> CandidateAnswer:
    """Generate a compact RCA answer directly from the evidence bundle."""

    hypothesis = choose_primary_hypothesis(bundle)
    if hypothesis is None:
        return CandidateAnswer(
            source=source,
            case_id=bundle.case_id,
            diagnosis_output="根因未能从可见证据中确定：当前没有足够的跨模态支撑证据定位单一主因。",
            trace_id=f"ontology-{bundle.case_id}",
        )
    evidence_sentences = []
    for item in hypothesis.support[:4]:
        if item.modality == "alarm" and len(evidence_sentences) >= 2:
            continue
        evidence_sentences.append(_evidence_sentence(item.modality, item.summary))
    if not evidence_sentences:
        evidence_sentences = [
            f"可见证据指向 {hypothesis.label}：{_clean_reason(hypothesis.reason)}"
        ]
    contradictions = ""
    if hypothesis.contradictions:
        contradictions = (
            " 仍需注意："
            + "；".join(_clean_reason(item) for item in hypothesis.contradictions[:2])
            + "。"
        )
    mechanism = _mechanism_phrase(hypothesis)
    reason = _clean_reason(hypothesis.reason)
    reason_clause = f"{reason}。" if reason and reason != mechanism else ""
    anchors = answer_anchor_sentences(bundle, hypothesis)
    anchor_clause = f"定位细节：{'；'.join(anchors)}。" if anchors else ""
    diagnosis = (
        f"根因：{hypothesis.label} 触发{mechanism}。"
        f"{reason_clause}{anchor_clause}关键证据："
        + "；".join(evidence_sentences)
        + f"。影响链路：{_impact_sentence(bundle.case_type, hypothesis)}"
        + f"处置建议：{_remediation_sentence(hypothesis)}"
        + contradictions
    )
    return CandidateAnswer(
        source=source,
        case_id=bundle.case_id,
        diagnosis_output=diagnosis,
        trace_id=_trace_id(hypothesis),
    )


def _evidence_sentence(modality: str, summary: str) -> str:
    prefix = {
        "alarm": "告警显示",
        "metric": "指标显示",
        "trace": "Trace 显示",
        "topology": "调用链显示",
        "log": "日志显示",
        "sql": "SQL 证据显示",
        "event": "变更/事件显示",
        "app": "应用画像显示",
        "custom_monitor": "业务监控显示",
    }.get(modality, "证据显示")
    return f"{prefix}：{clip_text(_clean_reason(summary), 320)}"


def _clean_reason(text: str) -> str:
    cleaned = PROCESS_TERM_RE.sub("", text or "")
    replacements = {
        "multi-signal  neighborhood": "多类观测信号",
        "multi-signal neighborhood": "多类观测信号",
        "near alarm window": "发生在告警窗口",
        "root cause": "根因",
        "abnormal trace span": "异常调用链 span",
        "HSF topology plus direct thread-pool evidence shows": "HSF 调用链和线程池证据显示",
        "SQL/TDDL evidence from": "SQL/TDDL 证据来自",
        "metric series": "指标序列",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ：:;；,.，。")
    return cleaned


def _mechanism_phrase(hypothesis: RootHypothesis) -> str:
    text = f"{hypothesis.kind} {hypothesis.label} {hypothesis.reason}".lower()
    kind = hypothesis.kind.lower()
    root_layer = hypothesis.root_layer.lower()
    if root_layer == "database":
        if "connection_pool" in text or "连接池" in text:
            return "连接池耗尽或连接获取阻塞"
        return "数据库慢 SQL 或表级访问异常"
    if root_layer == "change" or "change" in kind or "offline_capacity" in text:
        return "变更引入的服务容量或配置异常"
    if root_layer == "application" and (
        "data_quality" in text
        or "business_system_error" in text
        or "badrequest" in text
        or "numberformat" in text
        or "参数" in text
        or "脏数据" in text
    ):
        return "业务数据或参数契约异常"
    if kind == "pattern_hsf_downstream_timeout" or "downstream_timeout" in text:
        return "下游接口超时或 RPC 失败"
    if kind in {"hsf_service_method", "pattern_hsf_provider_error_qps_spike"}:
        return "HSF 下游接口错误或 RT 异常"
    if "security" in text or "攻击" in text or "恶意" in text:
        return "外部安全扫描/恶意请求"
    if "threadpool" in text or "thread pool" in text or "线程池" in text:
        return "HSF 线程池饱和或请求排队"
    if "sentinel" in text or "rate limit" in text or "限流" in text or "tc " in text:
        return "限流/快速拒绝"
    if "connection_pool" in text or "连接池" in text:
        return "连接池耗尽或连接获取阻塞"
    if "metaq" in text or "rocketmq" in text or "mq" in text:
        return "消息队列消费异常"
    if "cache" in text or "tair" in text or "redis" in text:
        return "缓存/Redis/Tair 异常"
    if "host" in text or "cpu" in text or "基础设施" in text:
        return "单机或基础设施异常"
    if "http_400" in text or "http_401" in text or "auth" in text:
        return "接入层 HTTP/鉴权异常"
    if "change" in text or "deploy" in text or "发布" in text:
        return "变更引入的服务异常"
    return "可见主因异常"


def _impact_sentence(case_type: str, hypothesis: RootHypothesis) -> str:
    if hypothesis.root_layer == "database":
        return "数据库侧耗时或错误放大到上游业务接口，最终表现为 RT 升高、超时或成功率下降。"
    if hypothesis.root_layer == "security":
        return "异常入口流量触发服务侧校验、拦截或异常返回，失败请求被计入业务/HSF 成功率指标。"
    if hypothesis.root_layer == "cache":
        return "缓存访问超时或错误拖慢业务链路，使上游接口在告警窗口内出现失败或超时。"
    if hypothesis.root_layer == "infrastructure":
        return "单机资源或宿主侧异常使该实例承接的请求处理变慢或失败，并向入口告警指标传导。"
    if "metaq" in hypothesis.kind.lower() or case_type.upper() == "METAQ":
        return "消息生产、投递或消费异常导致业务处理失败、堆积或单机资源升高，并触发对应告警。"
    return "该主因位于告警链路的上游或关键处理节点，能够解释成功率、RT、错误量或业务指标异常。"


def _remediation_sentence(hypothesis: RootHypothesis) -> str:
    mechanism = _mechanism_phrase(hypothesis)
    if "慢 SQL" in mechanism or "表级访问" in mechanism:
        return "优先按 SQL 指纹/表名还原执行计划，检查索引、扫描行数和锁等待，必要时限流或终止异常查询。"
    if "限流" in mechanism:
        return "先确认限流规则、调用量来源和阈值配置，必要时扩容、分流或调整保护策略，并验证失败率恢复。"
    if "线程池" in mechanism:
        return (
            "立即摘除或重启异常实例，检查线程栈和慢处理请求，恢复后验证 THREADPOOL_BUSY/超时消失。"
        )
    if "安全扫描" in mechanism:
        return "保留攻击样本，确认网关/应用侧拦截策略，过滤恶意参数并避免将拦截请求计入正常成功率口径。"
    if "消息队列" in mechanism:
        return "检查 topic/group 的队列分配、重试和消费异常，必要时重新均衡、限速或补偿消费。"
    return "优先隔离异常实体，结合对应日志、指标和调用链恢复容量或配置，随后验证告警指标回落。"
