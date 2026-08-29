from __future__ import annotations

import re
from collections.abc import Iterable

from tests.benchmarks.realrca_graph.features import entity_features
from tests.benchmarks.realrca_graph.models import EvidenceBundle, RootHypothesis

SQL_TABLE_LABEL_RE = re.compile(r"^[a-zA-Z0-9_.$-]{2,80}$")
ZERO_ONLY_FGC_RE = re.compile(
    r"metric=jvm_gc_fgc[^\n。；;]*"
    r"min=0(?:\.0+)?[^\n。；;]*max=0(?:\.0+)?[^\n。；;]*"
    r"avg=0(?:\.0+)?[^\n。；;]*last=0(?:\.0+)?",
    re.I,
)


def answer_anchor_sentences(
    bundle: EvidenceBundle,
    primary: RootHypothesis,
    *,
    limit: int = 4,
) -> list[str]:
    """Return support-backed RCA wording anchors for synthesized answers."""

    anchors: list[tuple[str, str]] = []
    anchors.extend(_anchors_for_hypothesis(primary, bundle.case_type))
    primary_groups = {group for group, _sentence in anchors}

    for hypothesis in bundle.hypotheses:
        if hypothesis.id == primary.id or has_hard_contradiction(hypothesis):
            continue
        if hypothesis.score < max(4.8, primary.score * 0.58):
            continue
        for group, sentence in _anchors_for_hypothesis(hypothesis, bundle.case_type):
            if group in primary_groups or not _allow_complementary_anchor(primary, group):
                continue
            anchors.append((group, sentence))
            primary_groups.add(group)
            break
        if len(anchors) >= limit:
            break

    return _dedupe(sentence for _group, sentence in anchors)[:limit]


def _anchors_for_hypothesis(hypothesis: RootHypothesis, case_type: str) -> list[tuple[str, str]]:
    text = _hypothesis_text(hypothesis)
    kind = hypothesis.kind.lower()
    layer = hypothesis.root_layer.lower()
    case_type_lower = case_type.lower()
    anchors: list[tuple[str, str]] = []

    table = _sql_table(hypothesis) if _is_sql_boundary(kind, layer, text) else ""
    if table and "write_table_rt" in text:
        anchors.append(("sql_write_rt", f"写RT异常表定位：定位到 {table} 表写入 RT 飙升"))
    elif table and _has_any(text, ("slow_sql", "慢 sql", "慢sql", "middleware_tddl", "sql_top")):
        anchors.append(("sql", f"慢 SQL 定位：异常集中在 {table} 表或对应 SQL 指纹"))

    if _is_external_downstream_timeout(kind, text):
        anchors.append(("downstream_service_timeout", "下游服务超时：下游外部服务连接超时或不可达"))
    elif _is_downstream_timeout(kind, text):
        anchors.append(
            ("downstream_timeout", "下游接口超时：调用下游服务接口出现 TIMEOUT/RPC_ERROR")
        )

    if _is_hsf_threadpool_boundary(kind, text):
        anchors.append(
            ("thread_pool", "下游线程池打满定位：下游 HSF provider 线程池饱和或拒绝请求")
        )

    if (
        layer == "middleware_limit"
        or kind == "pattern_limit"
        or _has_any(text, ("sentinel", "限流", "tcexception"))
    ):
        anchors.append(("limit", "接口限流：Sentinel/TC 规则触发快速拒绝或流控"))

    if _is_mq_cpu_case(kind, layer, text, case_type_lower):
        anchors.append(("mq_cpu", "MQ消费激增致CPU打高：MQ 消息消费量在告警窗口激增并推高 CPU"))
    elif _has_any(text, ("metaq", "rocketmq", "mq_spike", "message volume spike", "消息量")):
        anchors.append(("mq", "消息队列异常：topic/group 的生产或消费指标在告警窗口异常"))

    if _is_cache_boundary(kind, layer, text):
        if _has_any(text, ("hit", "命中", "tair", "redis", "jedis")):
            anchors.append(("cache_hit", "缓存命中率下降：缓存访问异常导致请求回源或超时"))
        if _has_any(text, ("read_qps", "热点", "hot key", "tair", "redis", "jedis")):
            anchors.append(("hot_key", "热点key定位：Redis/Tair 访问集中在热点 key 或热点缓存实例"))

    if kind == "pattern_host_anomaly" or (
        layer == "infrastructure"
        and _has_any(text, ("single-host", "target-host", "server_ip", "host_ip"))
    ):
        anchors.append(("host", "单机异常：异常集中在单机或目标 IP 上"))

    if (
        layer == "change"
        or _has_any(kind, ("change", "capacity"))
        or _has_any(text, ("offline_capacity", "缩容", "机器下线"))
    ):
        anchors.append(("change", "缩容变更关联：缩容、机器下线或发布变更与告警窗口同窗出现"))

    if _has_any(text, ("fullgc", "full gc", "metaspace", "fgc")) and not ZERO_ONLY_FGC_RE.search(
        text
    ):
        anchors.append(("full_gc", "Full GC：JVM Full GC 或内存压力导致服务处理能力下降"))
    elif kind == "pattern_jvm_gc_pressure" or _has_any(
        text,
        (
            "jvm_gc_count_delta",
            "jvm_gc_time_delta",
            "g1_young_generation",
            "g1_concurrent_gc",
            "gc pressure",
            "gc耗时",
        ),
    ):
        anchors.append(("jvm_gc_pressure", "JVM GC压力：GC次数或GC耗时升高导致服务处理能力下降"))

    if layer == "application" and _has_any(
        text, ("data_quality", "badrequest", "numberformat", "参数", "脏数据", "资格", "余额不足")
    ):
        if _has_any(text, ("crowd", "querytag", "人群")):
            anchors.append(
                ("crowd_dirty_data", "人群id脏数据：人群或标签查询参数异常触发业务校验失败")
            )
        else:
            anchors.append(("data_quality", "脏数据/参数异常：业务数据或参数契约不满足服务校验"))

    if layer == "security" or "security_scan" in kind:
        anchors.append(("security", "外部攻击探测：安全扫描/恶意请求触发异常流量或拦截"))

    return _dedupe_pairs(anchors)


def _hypothesis_text(hypothesis: RootHypothesis) -> str:
    support_text = " ".join(
        f"{item.name} {item.command} {item.summary}" for item in hypothesis.support
    )
    entity_text = " ".join(" ".join(values) for values in hypothesis.entities.values())
    return " ".join(
        [
            hypothesis.kind,
            hypothesis.label,
            hypothesis.root_layer,
            hypothesis.reason,
            entity_text,
            support_text,
        ]
    ).lower()


def _sql_table(hypothesis: RootHypothesis) -> str:
    for table in hypothesis.entities.get("sql_tables") or []:
        if SQL_TABLE_LABEL_RE.fullmatch(table):
            return table
    entities = entity_features({"label": hypothesis.label, "reason": hypothesis.reason})
    for table in entities.get("sql_tables") or []:
        if SQL_TABLE_LABEL_RE.fullmatch(table):
            return table
    label = hypothesis.label.strip()
    return label if SQL_TABLE_LABEL_RE.fullmatch(label) else ""


def _is_downstream_timeout(kind: str, text: str) -> bool:
    if kind == "pattern_hsf_downstream_timeout":
        return True
    return "downstream_timeout" in text or (
        "hsf" in text
        and _has_any(text, ("timeout", "rpc_error", "result=03", "result_codes={'03'"))
    )


def _is_external_downstream_timeout(kind: str, text: str) -> bool:
    return kind in {"pattern_external_dependency", "external_dependency_failure"} or _has_any(
        text,
        ("external dependency timeout", "no route to host", "connection timed out"),
    )


def _is_hsf_threadpool_boundary(kind: str, text: str) -> bool:
    if kind in {"hsf_threadpool_busy", "pattern_hsf_threadpool_timeout", "pattern_threadpool_busy"}:
        return True
    if _has_any(
        text,
        (
            "threadpool_busy",
            "thread pool is full",
            "provider threadpool",
            "provider-pool",
            "hsf线程",
            "线程池打满",
            "队列满",
        ),
    ):
        return True
    return "hsf_service_method" in kind and _has_any(
        text,
        (
            "middleware_hsf_consumer_service_method_error_qps",
            "hsf消费者接口异常qps",
            "provider_service_method_error_qps",
        ),
    )


def _is_mq_cpu_case(kind: str, layer: str, text: str, case_type_lower: str) -> bool:
    return (
        case_type_lower == "cpu"
        and (layer == "message_queue" or "mq" in kind or _has_any(text, ("metaq", "rocketmq")))
        and _has_any(text, ("qps", "spike", "rising", "消费", "消息"))
    )


def _is_cache_boundary(kind: str, layer: str, text: str) -> bool:
    return layer == "cache" or "cache" in kind or _has_any(text, ("tair", "redis", "jedis"))


def _has_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle.lower() in text for needle in needles)


def _dedupe(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _dedupe_pairs(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group, sentence in values:
        if group in seen:
            continue
        seen.add(group)
        output.append((group, sentence))
    return output


def has_hard_contradiction(hypothesis: RootHypothesis) -> bool:
    """Return whether a hypothesis has more than a support-depth warning."""

    soft_markers = (
        "fewer than two concrete evidence modalities",
        "service-dependency hypothesis is not directly backed by trace evidence",
    )
    return any(
        not any(marker in item for marker in soft_markers) for item in hypothesis.contradictions
    )


def _is_sql_boundary(kind: str, layer: str, text: str) -> bool:
    return (
        layer == "database"
        or kind
        in {
            "evidence_sql",
            "pattern_slow_sql",
            "pattern_tddl_repeated_query_fanout",
            "sql_log_error",
            "rds_sql_stat",
            "rds_sql_detail",
            "app_sql_error",
        }
        or _has_any(text, ("middleware_tddl", "sql_top", "slow_sql", "慢 sql", "慢sql"))
    )


def _allow_complementary_anchor(primary: RootHypothesis, group: str) -> bool:
    layer = primary.root_layer.lower()
    kind = primary.kind.lower()
    if kind in {"pattern_external_dependency", "external_dependency_failure"}:
        return group in {"full_gc", "jvm_gc_pressure"}
    if layer == "middleware_limit" or kind == "pattern_limit":
        return group in {"cache_hit", "hot_key"}
    if layer == "change":
        return group in {"downstream_timeout", "full_gc", "jvm_gc_pressure"}
    if layer == "service_dependency":
        return group in {"thread_pool", "downstream_timeout", "change"}
    return group in {"full_gc", "jvm_gc_pressure"}
