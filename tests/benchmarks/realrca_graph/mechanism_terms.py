from __future__ import annotations

import re
from collections.abc import Sequence

from tests.benchmarks.realrca_graph.features import token_features

NEGATION_START_RE = re.compile(
    r"排除|不是|并非|无证据|未发现|不支持|而非|不将|not\s+(?:the\s+)?root",
    re.IGNORECASE,
)
NEGATION_SUFFIX_RE = re.compile(r"非根因|旁路|症状", re.IGNORECASE)

ROOT_CHANGING_RAW_MECHANISMS = {
    "auth_failure",
    "cache_timeout",
    "connection_pool",
    "dns_failure",
    "hsf_threadpool_busy",
    "http_400",
    "infra_event",
    "jvm_gc",
    "metaq_business_failure",
    "mq_duplicate_conflict",
    "pod_event",
    "runtime_limit",
    "slow_sql",
    "sql_error",
    "timeout",
}
SOFT_RAW_MECHANISMS = {"change_event", "data_quality", "duplicate_key"}

RAW_MECHANISM_PROBE_MARKERS = {
    "auth_failure": {"auth", "buc", "sso", "401", "login", "登录", "鉴权", "认证"},
    "cache_timeout": {"cache", "redis", "tair", "timeout", "缓存", "超时"},
    "change_event": {"change", "deploy", "publish", "normandy", "aone", "变更", "发布"},
    "connection_pool": {"connection", "conn", "pool", "druid", "连接池"},
    "dns_failure": {"dns", "address", "registry", "vip", "地址", "注册中心"},
    "duplicate_key": {"duplicate", "dup", "unique", "key", "重复", "唯一键"},
    "hsf_threadpool_busy": {
        "hsf",
        "threadpool",
        "threadpool_busy",
        "busy",
        "thread",
        "pool",
        "线程池",
        "打满",
        "耗尽",
        "饱和",
    },
    "http_400": {"http", "nginx", "400", "uri"},
    "infra_event": {"infra", "ecs", "host", "node", "pod", "evict", "单机", "主机", "机器"},
    "jvm_gc": {
        "jvm",
        "gc",
        "g1",
        "fullgc",
        "metaspace",
        "memory",
        "内存",
        "full gc",
        "stop-the-world",
        "stw",
    },
    "metaq_business_failure": {"mq", "metaq", "rocketmq", "consume", "consumer", "消息", "消费"},
    "mq_duplicate_conflict": {
        "duplicate",
        "updatewithversion",
        "update_error",
        "version",
        "conflict",
        "幂等",
        "乐观锁",
        "重投",
        "重复",
        "更新失败",
    },
    "pod_event": {"pod", "evict", "k8s", "runtime", "驱逐"},
    "runtime_limit": {"sentinel", "limit", "flow", "tc", "block", "throttle", "限流", "流控"},
    "slow_sql": {"sql", "tddl", "rds", "mysql", "table", "慢sql", "慢查询", "锁等待"},
    "sql_error": {"sql", "tddl", "rds", "mysql", "error", "sql异常"},
    "timeout": {"timeout", "slow", "rt", "超时"},
}

NEGATION_MECHANISM_MARKERS = {
    "auth_failure": {"auth", "buc", "sso", "401", "login", "登录", "鉴权", "认证"},
    "cache_timeout": {"cache", "redis", "tair", "jedis", "缓存"},
    "change_event": {"change", "deploy", "publish", "normandy", "aone", "变更", "发布"},
    "connection_pool": {"connection pool", "conn pool", "druid", "连接池"},
    "dns_failure": {"dns", "registry", "vip", "地址", "注册中心", "no provider"},
    "duplicate_key": {"duplicate", "unique", "key", "重复", "唯一键"},
    "hsf_threadpool_busy": {
        "threadpool",
        "threadpool_busy",
        "thread pool",
        "pool is full",
        "线程池",
    },
    "http_400": {"http 400", "nginx", "uri"},
    "infra_event": {"infra", "ecs", "host", "node", "pod", "evict", "单机", "主机", "机器"},
    "jvm_gc": {
        "jvm",
        "gc",
        "g1",
        "fullgc",
        "full gc",
        "metaspace",
        "memory",
        "内存",
        "stop-the-world",
        "stw",
    },
    "metaq_business_failure": {"metaq", "rocketmq", "consume", "consumer", "消息", "消费"},
    "mq_duplicate_conflict": {
        "duplicate",
        "updatewithversion",
        "update_error",
        "version",
        "conflict",
        "幂等",
        "乐观锁",
        "重投",
        "重复",
        "更新失败",
    },
    "pod_event": {"pod", "evict", "k8s", "runtime", "驱逐"},
    "runtime_limit": {"sentinel", "flow control", "tc", "blockexception", "限流", "流控"},
    "slow_sql": {"slow sql", "慢sql", "慢查询", "锁等待"},
    "sql_error": {"sql error", "sql异常", "tddl-", "sqlexception"},
    "timeout": {"timeout", "超时"},
}
ENTITY_TERM_STOPWORDS = {
    "address",
    "block",
    "cache",
    "connection",
    "consumer",
    "error",
    "failure",
    "fullgc",
    "limit",
    "network",
    "provider",
    "registry",
    "sentinel",
    "service",
    "thread",
    "threadpool",
    "timeout",
}


def mechanism_markers(raw_mechanisms: Sequence[str]) -> set[str]:
    """Return marker tokens used to compare mechanism families."""

    markers: set[str] = set()
    for mechanism in raw_mechanisms:
        normalized = mechanism.lower()
        markers.add(normalized)
        markers.update(part for part in normalized.split("_") if len(part) >= 2)
        markers.update(RAW_MECHANISM_PROBE_MARKERS.get(normalized, set()))
    return markers


def baseline_excluded_mechanisms(
    baseline_text: str,
    raw_mechanisms: Sequence[str],
) -> set[str]:
    """Return raw mechanisms explicitly negated by the current best answer."""

    clauses = negative_clauses(baseline_text)
    if not clauses:
        return set()
    excluded: set[str] = set()
    for mechanism in raw_mechanisms:
        markers = NEGATION_MECHANISM_MARKERS.get(mechanism.lower(), {mechanism.lower()})
        if any(_contains_marker(clauses, marker) for marker in markers):
            excluded.add(mechanism)
    return excluded


def baseline_negated_mechanisms_in_text(
    baseline_text: str,
    target_text: str,
) -> set[str]:
    """Return mechanisms that ``target_text`` promotes but baseline negates."""

    target_mechanisms = mechanisms_in_text(target_text) - {"timeout"}
    if not target_mechanisms:
        return set()
    clauses = negative_clause_list(baseline_text)
    if not clauses:
        return set()
    excluded: set[str] = set()
    for mechanism in target_mechanisms:
        markers = NEGATION_MECHANISM_MARKERS.get(mechanism.lower(), {mechanism.lower()})
        matching_clauses = [
            clause
            for clause in clauses
            if any(_contains_marker(clause, marker) for marker in markers)
        ]
        if not matching_clauses:
            continue
        if all(_target_uses_different_entities(target_text, clause) for clause in matching_clauses):
            continue
        excluded.add(mechanism)
    return excluded


def mechanisms_in_text(text: str) -> set[str]:
    """Infer high-level RCA mechanisms from visible answer or hypothesis text."""

    lowered = text.lower()
    mechanisms: set[str] = set()
    for mechanism, markers in NEGATION_MECHANISM_MARKERS.items():
        if _contains_marker(lowered, mechanism) or any(
            _contains_marker(lowered, marker) for marker in markers
        ):
            mechanisms.add(mechanism)
    return mechanisms


def negative_clauses(text: str) -> str:
    """Extract clauses that explicitly negate or demote candidate mechanisms."""

    return " ".join(negative_clause_list(text))


def negative_clause_list(text: str) -> list[str]:
    """Extract individual lower-cased negative clauses."""

    clauses: list[str] = []
    for raw_clause in re.split(r"(?<=[。；;\n])", text):
        if not raw_clause.strip():
            continue
        start_match = NEGATION_START_RE.search(raw_clause)
        if start_match is not None:
            clauses.append(raw_clause[start_match.start() :].lower())
            continue
        suffix_match = NEGATION_SUFFIX_RE.search(raw_clause)
        if suffix_match is not None:
            begin = max(0, suffix_match.start() - 80)
            clauses.append(raw_clause[begin:].lower())
    return clauses


def _target_uses_different_entities(target_text: str, negative_clause: str) -> bool:
    target_entities = _root_entities(target_text)
    clause_entities = _root_entities(negative_clause)
    return bool(target_entities and clause_entities and not target_entities & clause_entities)


def _root_entities(text: str) -> set[str]:
    tokens = token_features(text)
    entities = {
        token
        for token in tokens
        if token.startswith(("app:", "service:", "method:", "ip:", "rds:", "sql_table:", "sql_id:"))
    }
    entities.update(_entity_like_terms(tokens))
    return entities


def _entity_like_terms(tokens: set[str]) -> set[str]:
    output: set[str] = set()
    for token in tokens:
        if not token.startswith("term:"):
            continue
        value = token.removeprefix("term:")
        if value in ENTITY_TERM_STOPWORDS:
            continue
        if len(value) >= 6 or "-" in value or "." in value:
            output.add(token)
    return output


def _contains_marker(text: str, marker: str) -> bool:
    if not marker:
        return False
    lowered = marker.lower()
    if (
        lowered.isascii()
        and lowered.replace("-", "").replace("_", "").isalnum()
        and len(lowered) <= 3
    ):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(lowered)}(?![a-z0-9])", text))
    return lowered in text
