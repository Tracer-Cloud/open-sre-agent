from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from tests.benchmarks.realrca_graph.features import (
    clip_text,
    entity_features,
    infer_modality,
    infer_root_layer,
    token_features,
)
from tests.benchmarks.realrca_graph.models import EvidenceBundle, EvidenceItem, RootHypothesis
from tests.benchmarks.realrca_graph.root_patterns import pattern_root_candidates
from tests.benchmarks.realrca_graph.summaries import compact_evidence_summary
from tests.benchmarks.realrca_graph.summary_cache import compact_evidence_summary_cached
from tests.benchmarks.realrca_graph.topology import (
    topology_evidence_items,
    topology_root_candidates,
)

OPAQUE_EVENT_LABEL_RE = re.compile(
    r"^(?:[0-9a-f]{16}|[0-9a-f]{32}|[0-9a-f]{40}-cms|e-[0-9a-z]{8,})$",
    re.I,
)
CONCRETE_CACHE_LABEL_RE = re.compile(r"(?:\b(?:jedis|tair)@|(?<![0-9a-z])r-[0-9a-z]{8,})", re.I)
CACHE_TIMEOUT_SIGNAL_RE = re.compile(
    r"SocketTimeoutException|JedisConnectionException|read timed out|query timeout|"
    r"connection timed out|connect timeout|超时",
    re.I,
)
GENERIC_WRAPPER_EXCEPTION_RE = re.compile(
    r"(?:HttpClientException|BizException|NullPointerException|RuntimeException|HSFException)$"
)
SUMMARY_TABLE_RE = re.compile(r"tables=\{['\"]?([A-Z0-9_.$-]{2,80})", re.IGNORECASE)
METRIC_TABLE_LABEL_RE = re.compile(r"\btable=([A-Z0-9_.$-]{2,80})", re.IGNORECASE)
TDDL_TABLE_RT_RE = re.compile(r"\bmiddleware_tddl_(?:read|write)_table_rt\b", re.IGNORECASE)
TDDL_TABLE_SUCCESS_RATE_RE = re.compile(
    r"\bmiddleware_tddl_(?:read|write)_table_success_rate\b",
    re.IGNORECASE,
)
TDDL_TABLE_METRIC_RE = re.compile(r"\bmiddleware_tddl_(?:read|write)_table_", re.IGNORECASE)
METRIC_STAT_RE = re.compile(
    r"\b(?P<key>min|max|avg|last)\s*=\s*(?P<value>[0-9]+(?:\.[0-9]+)?)", re.IGNORECASE
)
ZERO_ONLY_METRIC_SUMMARY_RE = re.compile(
    r"\bmin=0(?:\.0+)?(?:,|\b).*?\bmax=0(?:\.0+)?(?:,|\b).*?"
    r"\bavg=0(?:\.0+)?(?:,|\b).*?\blast=0(?:\.0+)?(?:,|\b)",
    re.IGNORECASE | re.DOTALL,
)
SQL_DURATION_RE = re.compile(
    r"\b(?:duration(?:_ms)?|spanClient|spanServer)\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)\s*(?:ms)?",
    re.IGNORECASE,
)
SQL_SPAN_TABLE_RE = re.compile(
    r"\bTDDL_[A-Z]+@[^\s:\x1a]+:(?P<table>[^\s\x1a]+)(?:\x1a[0-9a-zA-Z_.$-]+)?"
    r"(?P<tail>.{0,180})",
    re.IGNORECASE,
)
IP_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
COMPACT_METRIC_LABEL_RE = re.compile(r"\b(app_group|remote_app_name|service|method)=([^,\]\s]+)")
JSON_METRIC_LABEL_BLOCK_RE = re.compile(r'"labels"\s*:\s*\{([^}]*)', re.DOTALL)
JSON_METRIC_LABEL_RE = re.compile(r'"(app_group|remote_app_name|service|method)"\s*:\s*"([^"]*)"')
COMPACT_METRIC_BLOCK_RE = re.compile(r"\[([^\]]+)\]")
HSF_METRIC_NAMES = (
    "middleware_hsf_consumer_service_method_error_qps",
    "middleware_hsf_consumer_service_method_rt",
    "middleware_hsf_consumer_service_method_success_rate",
    "middleware_hsf_provider_service_method_error_qps",
    "middleware_hsf_provider_service_method_rt",
    "middleware_hsf_provider_service_method_success_rate",
)
HSF_PROVIDER_LIMIT_MIN_MAX = 500.0
HSF_PROVIDER_LIMIT_MIN_AVG = 50.0
APP_LOG_HSF_THREADPOOL_SIGNAL_RE = re.compile(
    r"kind=hsf_threadpool_busy\s+label=THREADPOOL_BUSY:(?P<ip>"
    r"(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d))"
    r"\s+count=(?P<count>\d+)",
    re.I,
)
APP_LOG_PROVIDER_IPS_RE = re.compile(r"provider_ips=\[(?P<body>[^\]]+)\]", re.I)
APP_LOG_THREADPOOL_COUNT_RE = re.compile(
    r"(?:THREADPOOL_BUSY['\"]?\s*:\s*|count=)(?P<count>\d+)", re.I
)
BUSINESS_SYSTEM_ERROR_SIGNAL_RE = re.compile(
    r"kind=business_system_error\s+label=(?P<label>.*?)\s+count=\d+",
    re.I,
)
BUSINESS_ERROR_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{3,}|[\u4e00-\u9fff]{2,}", re.I)
GENERIC_BUSINESS_ERROR_TOKENS = {
    "business",
    "business_system_error",
    "biz_error",
    "error",
    "system_error",
}
PATTERN_SHADOW_KINDS: dict[str, set[str]] = {
    "pattern_limit": {"app_log_limit"},
    "pattern_threadpool_busy": {"hsf_threadpool_busy"},
    "pattern_external_dependency": {"external_dependency_failure"},
    "pattern_connection_pool": {"connection_pool_exhausted", "stale_db_connection"},
    "pattern_data_quality": {"app_sql_error", "db_access_failure", "sql_log_error"},
    "pattern_slow_sql": {
        "evidence_sql",
        "sql_log_error",
        "app_sql_error",
        "rds_sql_stat",
        "rds_sql_detail",
    },
}


def _case_value(graph_context: dict[str, Any], key: str) -> str:
    case = graph_context.get("case")
    if isinstance(case, dict):
        value = case.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _ontology(graph_context: dict[str, Any]) -> list[str]:
    raw = graph_context.get("ontology")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return []


def _score_from_raw(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dedupe_evidence(items: Iterable[EvidenceItem], limit: int) -> list[EvidenceItem]:
    output: list[EvidenceItem] = []
    seen: set[tuple[str, str, str]] = set()
    for item in sorted(items, key=lambda value: (-value.score, value.name, value.summary)):
        if _is_zero_only_full_gc_metric(item):
            continue
        if item.name.startswith(("sls_sql_", "sls_access_", "sls_app_", "rds_sql_")):
            if _is_empty_observation(item):
                continue
            key = (item.modality, "", item.summary)
        else:
            key = (item.modality, item.name, item.summary)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
        if len(output) >= limit:
            break
    return output


def evidence_items_from_graph(
    graph_context: dict[str, Any], *, limit: int = 32
) -> list[EvidenceItem]:
    """Parse graph evidence rows into compact provenance-backed observations."""

    output: list[EvidenceItem] = []
    for index, raw in enumerate(graph_context.get("evidence") or [], start=1):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or f"evidence_{index}")
        command = str(raw.get("command") or "")
        raw_ref = str(raw.get("raw_path") or raw.get("raw_ref") or "")
        summary = compact_evidence_summary_cached(
            name,
            command,
            raw_ref,
            raw.get("summary") or raw,
        )
        returncode = raw.get("returncode")
        base_score = 1.0 if returncode in (None, 0, "0") else 0.2
        modality = infer_modality(name, command, summary)
        if modality != "other":
            base_score += 0.35
        output.append(
            EvidenceItem(
                id=f"e{index}",
                name=name,
                modality=modality,
                summary=summary,
                command=command,
                raw_ref=raw_ref,
                score=round(base_score, 3),
            )
        )
    output.extend(topology_evidence_items(graph_context, start_index=len(output) + 1, limit=8))
    return _dedupe_evidence(output, limit)


def _support_for_candidate(
    candidate: dict[str, Any],
    evidence: list[EvidenceItem],
    *,
    limit: int,
) -> list[EvidenceItem]:
    kind = str(candidate.get("kind") or "")
    candidate_tokens = token_features(candidate)
    scored: list[tuple[float, EvidenceItem]] = []
    for item in evidence:
        if _is_empty_observation(item):
            continue
        if item.modality == "topology" and str(candidate.get("kind") or "") not in {
            "topology_trace_path",
            "pattern_hsf_cold_start_capacity",
            "pattern_hsf_downstream_timeout",
            "pattern_tddl_repeated_query_fanout",
            "pattern_hsf_threadpool_timeout",
        }:
            continue
        evidence_tokens = token_features(
            {"name": item.name, "summary": item.summary, "command": item.command}
        )
        overlap = candidate_tokens & evidence_tokens
        modality_bonus = (
            0.5
            if infer_modality(candidate.get("kind"), candidate.get("label")) == item.modality
            else 0.0
        )
        source_bonus = _source_affinity_bonus(kind, item.name)
        alarm_penalty = 0.6 if item.modality == "alarm" else 0.0
        if not overlap and modality_bonus == 0.0 and source_bonus == 0.0:
            continue
        score = len(overlap) + modality_bonus + source_bonus + item.score - alarm_penalty
        if score <= 1.0 and not overlap:
            continue
        scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    ranked = [item for _score, item in scored]
    concrete = [item for item in ranked if item.modality != "other"]
    context = [item for item in ranked if item.modality == "other"]
    diverse: list[EvidenceItem] = []
    seen_modalities: set[str] = set()
    for item in concrete:
        if item.modality in seen_modalities:
            continue
        seen_modalities.add(item.modality)
        diverse.append(item)
        if len(diverse) >= limit:
            return diverse
    seen_ids = {item.id for item in diverse}
    for item in [*concrete, *context]:
        if item.id in seen_ids:
            continue
        diverse.append(item)
        seen_ids.add(item.id)
        if len(diverse) >= limit:
            break
    return diverse


def _source_affinity_bonus(kind: str, evidence_name: str) -> float:
    if kind in {
        "app_log_limit",
        "app_sql_error",
        "business_system_error",
        "connection_pool_exhausted",
        "stale_db_connection",
        "db_access_failure",
        "external_dependency_failure",
        "heavy_business_query",
        "hsf_threadpool_busy",
        "metaq_duplicate_update_conflict",
        "metaq_business_failure",
        "metaq_broker_failure",
        "auth_session_failure",
        "pod_runtime_event",
    }:
        return 2.0 if evidence_name.startswith("sls_app_") else 0.0
    if kind == "pattern_auth_session_failure":
        return (
            2.0
            if evidence_name.startswith(("trace_", "sls_access_", "sls_app_", "log_error"))
            else 0.0
        )
    if kind == "pattern_metaq_broker_failure":
        return (
            2.0 if evidence_name.startswith(("sls_app_", "log_error_list", "trace_get_")) else 0.0
        )
    if kind == "pattern_metaq_duplicate_update_conflict":
        return (
            2.0 if evidence_name.startswith(("sls_app_", "log_error_list", "trace_get_")) else 0.0
        )
    if kind == "sql_log_error":
        return 2.0 if evidence_name.startswith("sls_sql_") else 0.0
    if kind in {"rds_sql_stat", "rds_sql_detail"}:
        return 2.0 if evidence_name.startswith("rds_sql_") else 0.0
    if kind == "http_access_error":
        return 2.0 if evidence_name.startswith("sls_access_") else 0.0
    if kind == "custom_monitor_signal":
        return 2.0 if evidence_name.startswith(("metric_custom_", "monitor_fields_")) else 0.0
    if kind == "pattern_infra_event":
        return 2.0 if evidence_name.startswith("event_") else 0.0
    if kind == "pattern_app_publish_data_quality":
        return 2.0 if evidence_name.startswith("event_") else 0.0
    if kind == "pattern_downstream_offline_change":
        return 2.0 if evidence_name.startswith("event_") else 0.0
    if kind in {
        "pattern_hsf_downstream_timeout",
        "pattern_hsf_provider_subset_rpc_error",
        "pattern_tddl_repeated_query_fanout",
    }:
        return 1.5 if evidence_name.startswith(("trace_", "topology_")) else 0.0
    return 0.0


def _is_empty_observation(item: EvidenceItem) -> bool:
    summary = item.summary.lower()
    if summary.strip() == "[]":
        return True
    if _is_zero_only_full_gc_metric(item):
        return True
    empty_markers = (
        "series_count=0",
        '"series_count": 0',
        "spans=0 top=",
        "changes=0 top=",
        "events count=0",
        "access_logs count=0",
        "sql_logs count=0",
        "rds_sql count=0",
        "app_logs count=0",
        '"pattern_count": 0',
        '"total_errors": 0',
        '"count": 0',
        "无异常日志",
    )
    return any(marker in summary for marker in empty_markers)


def _is_zero_only_full_gc_metric(item: EvidenceItem) -> bool:
    summary = item.summary.lower()
    return (
        item.modality == "metric"
        and "metric=jvm_gc_fgc" in summary
        and ZERO_ONLY_METRIC_SUMMARY_RE.search(summary) is not None
    )


def _modalities_from_candidate(raw: dict[str, Any]) -> set[str]:
    props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
    modalities: set[str] = set()
    if isinstance(props.get("trace_ids"), list) and props.get("trace_ids"):
        modalities.add("trace")
    top_signals = props.get("top_signals")
    if not isinstance(top_signals, list):
        return modalities
    for signal in top_signals:
        if not isinstance(signal, dict):
            continue
        modality = infer_modality(
            signal.get("kind"),
            signal.get("label"),
            signal.get("props"),
            signal.get("reason"),
        )
        if modality != "other":
            modalities.add(modality)
    return modalities


def _alarm_label(summary: str) -> str:
    app_match = re.search(r"\bapp=([^\s]+)", summary)
    metric_match = re.search(r"\bmetric=([^\s]+)", summary)
    title_match = re.search(r"\btitle=([^=]+?)\s+metric=", summary)
    app = app_match.group(1).strip() if app_match else ""
    metric = metric_match.group(1).strip() if metric_match else ""
    title = title_match.group(1).strip() if title_match else ""
    if app and metric:
        return f"{app}:{metric}"
    if app and title:
        return f"{app}:{title}"
    return app or metric or title


def _entity_label(features: dict[str, list[str]]) -> str:
    for key in (
        "sql_tables",
        "sql_ids",
        "sql_dbs",
        "rds_instances",
        "services",
        "exceptions",
        "apps",
        "keywords",
    ):
        values = features.get(key) or []
        if values:
            return values[0]
    return ""


def _fallback_root_candidates(
    evidence: list[EvidenceItem], *, case_type: str = ""
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    case_type_upper = case_type.upper()
    for item in evidence:
        if item.modality in {"other", "topology"}:
            continue
        if (
            item.modality == "sql"
            and item.name.startswith("trace_get")
            and case_type_upper != "TDDL"
        ):
            continue
        if item.name.startswith("rds_sql_") and _is_low_specificity_rds_sql(item.summary):
            continue
        if item.modality != "alarm" and _is_empty_observation(item):
            continue
        features = entity_features(
            {"name": item.name, "summary": item.summary, "command": item.command}
        )
        if item.modality == "alarm":
            label = _alarm_label(item.summary)
        elif item.modality == "sql":
            label = _sql_entity_label(item.summary, features)
        else:
            label = _entity_label(features)
        if not label:
            continue
        key = f"{item.modality}:{label}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "kind": f"evidence_{item.modality}",
                "label": label,
                "score": 2.8 + item.score,
                "reason": f"fallback hypothesis from {item.name}: {clip_text(item.summary, 220)}",
                "props": {
                    "evidence_id": item.id,
                    "modality": item.modality,
                    "source": item.name,
                    "entities": features,
                },
            }
        )
    return candidates


def _sql_root_candidates(
    evidence: list[EvidenceItem], *, case_type: str = ""
) -> list[dict[str, Any]]:
    candidates_by_label: dict[str, dict[str, Any]] = {}
    case_type_upper = case_type.upper()
    for item in evidence:
        if item.modality != "sql" or _is_empty_observation(item):
            continue
        if item.name.startswith("trace_get") and case_type_upper not in {"TDDL", "自定义监控"}:
            continue
        if item.name.startswith("rds_sql_") and _is_low_specificity_rds_sql(item.summary):
            continue
        features = entity_features(
            {"name": item.name, "summary": item.summary, "command": item.command}
        )
        label = _sql_entity_label(item.summary, features)
        if not label:
            continue
        evidence_strength = _sql_evidence_strength(item.summary)
        candidate = {
            "kind": "evidence_sql",
            "label": label,
            "score": 5.6 + min(item.score, 1.5) + evidence_strength,
            "reason": f"SQL/TDDL evidence from {item.name}: {clip_text(item.summary, 260)}",
            "props": {
                "evidence_id": item.id,
                "modality": "sql",
                "source": item.name,
                "entities": features,
                "max_duration_ms": _max_sql_duration_ms(item.summary),
            },
        }
        current = candidates_by_label.get(label)
        if current is None or float(candidate["score"]) > float(current["score"]):
            candidates_by_label[label] = candidate
    return sorted(
        candidates_by_label.values(),
        key=lambda value: (-float(value["score"]), str(value["label"])),
    )


def _is_low_specificity_rds_sql(summary: str) -> bool:
    lower = summary.lower()
    if "synthetic_sql_load" in lower or "synthetic_load=true" in lower:
        return True
    return "sql_id=" in lower and "table=" not in lower


def _sql_evidence_strength(summary: str) -> float:
    duration_ms = _max_sql_duration_ms(summary)
    score = 0.0
    if duration_ms >= 1000:
        score += min(1.35, duration_ms / 3000.0)
    if TDDL_TABLE_RT_RE.search(summary):
        maximum = _metric_stat(summary, "max")
        if maximum >= 30.0:
            score += 2.4
        elif maximum >= 5.0:
            score += min(1.2, maximum / 25.0)
        if "trend=rising" in summary.lower():
            score += 0.3
    if TDDL_TABLE_SUCCESS_RATE_RE.search(summary) and METRIC_TABLE_LABEL_RE.search(summary):
        minimum = _first_metric_stat(summary, "min")
        average = _first_metric_stat(summary, "avg")
        if minimum is not None and minimum <= 0.01 and (average is None or average < 0.995):
            score += 2.7
        elif minimum is not None and minimum < 0.98:
            score += min(1.5, (0.98 - minimum) * 3.0)
        if "trend=falling" in summary.lower():
            score += 0.35
    if re.search(r"\b(?:slow|慢sql|慢查询|lock wait|锁等待)\b", summary, re.IGNORECASE):
        score += 0.35
    return round(score, 3)


def _metric_stat(summary: str, key: str) -> float:
    values: list[float] = []
    for match in METRIC_STAT_RE.finditer(summary):
        if match.group("key").lower() != key:
            continue
        try:
            values.append(float(match.group("value")))
        except ValueError:
            continue
    return max(values, default=0.0)


def _first_metric_stat(summary: str, key: str) -> float | None:
    block_match = COMPACT_METRIC_BLOCK_RE.search(summary)
    text = block_match.group(1) if block_match else summary
    for match in METRIC_STAT_RE.finditer(text):
        if match.group("key").lower() != key:
            continue
        try:
            return float(match.group("value"))
        except ValueError:
            return None
    return None


def _max_sql_duration_ms(summary: str) -> float:
    values = []
    for match in SQL_DURATION_RE.finditer(summary):
        try:
            values.append(float(match.group(1)))
        except ValueError:
            continue
    return max(values, default=0.0)


def _hsf_root_candidates(
    evidence: list[EvidenceItem], *, case_type: str = ""
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    alarm_side = _hsf_alarm_side(evidence)
    for item in evidence:
        if item.modality != "metric" or "middleware_hsf_" not in f"{item.name} {item.summary}":
            continue
        metric_side = _hsf_metric_side(item.name)
        for labels in _metric_label_sets(item.summary):
            service = str(labels.get("service") or "").strip()
            method = str(labels.get("method") or "").strip()
            app_group = str(labels.get("app_group") or "").strip()
            remote_app = str(labels.get("remote_app_name") or "").strip()
            if not service and not method:
                continue
            key = (app_group, service, method, remote_app)
            if key in seen:
                continue
            seen.add(key)
            label = _hsf_label(
                app_group=app_group,
                service=service,
                method=method,
                remote_app=remote_app,
            )
            mechanism_props = _hsf_metric_mechanism_props(item, labels, case_type=case_type)
            reason = f"HSF metric labels from {item.name}: {clip_text(item.summary, 260)}"
            if mechanism_props.get("failure_mode") == "qps_spike_runtime_limit":
                reason += (
                    "; high provider error_qps spike suggests interface QPS spike and 接口限流"
                )
            if (
                alarm_side == "consumer"
                and metric_side == "consumer"
                and "service_method" in item.name
            ):
                reason += "; consumer-side HSF service-method anomaly indicates downstream interface error or 超时"
            candidates.append(
                {
                    "kind": "hsf_service_method",
                    "label": label,
                    "score": _hsf_metric_candidate_score(
                        item,
                        labels,
                        case_type=case_type,
                        alarm_side=alarm_side,
                    ),
                    "reason": reason,
                    "props": {
                        "evidence_id": item.id,
                        "modality": "metric",
                        "source": item.name,
                        "app_group": app_group,
                        "service": service,
                        "method": method,
                        "remote_app_name": remote_app,
                        "metric_side": metric_side,
                        "alarm_side": alarm_side,
                        **mechanism_props,
                    },
                }
            )
    return candidates


def _hsf_app_log_root_candidates(
    evidence: list[EvidenceItem], *, case_type: str = ""
) -> list[dict[str, Any]]:
    if case_type.upper() not in {"HSF", "自定义监控"}:
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence:
        if item.modality != "log" or not item.name.startswith("sls_app_"):
            continue
        if _is_empty_observation(item):
            continue
        lower = item.summary.lower()
        if (
            "hsf_threadpool_busy" not in lower
            and "threadpool_busy" not in lower
            and "thread pool is full" not in lower
        ):
            continue
        provider_ip = _threadpool_provider_ip(item.summary)
        count = _threadpool_signal_count(item.summary)
        label = f"THREADPOOL_BUSY:{provider_ip}" if provider_ip else "THREADPOOL_BUSY"
        if label in seen:
            continue
        seen.add(label)
        candidates.append(
            {
                "kind": "hsf_threadpool_busy",
                "label": label,
                "score": 8.2 + min(item.score, 1.2) + min(0.5, count / 50.0),
                "reason": (
                    f"structured HSF provider thread-pool signal from {item.name}: "
                    f"{clip_text(item.summary, 280)}"
                ),
                "props": {
                    "evidence_id": item.id,
                    "modality": "log",
                    "source": item.name,
                    "signal_kind": "hsf_threadpool_busy",
                    "provider_ip": provider_ip,
                    "count": count,
                },
            }
        )
    return sorted(candidates, key=lambda value: (-float(value["score"]), str(value["label"])))


def _threadpool_provider_ip(summary: str) -> str:
    match = APP_LOG_HSF_THREADPOOL_SIGNAL_RE.search(summary)
    if match:
        return match.group("ip")
    provider_match = APP_LOG_PROVIDER_IPS_RE.search(summary)
    if provider_match:
        ips = IP_RE.findall(provider_match.group("body"))
        if ips:
            return ips[0]
    ips = IP_RE.findall(summary)
    return ips[0] if ips else ""


def _threadpool_signal_count(summary: str) -> int:
    match = APP_LOG_HSF_THREADPOOL_SIGNAL_RE.search(summary)
    if match:
        return int(match.group("count"))
    counts: list[int] = []
    for count_match in APP_LOG_THREADPOOL_COUNT_RE.finditer(summary):
        try:
            counts.append(int(count_match.group("count")))
        except ValueError:
            continue
    return max(counts, default=1)


def _metric_label_sets(summary: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(summary)
    except json.JSONDecodeError:
        return _compact_metric_label_sets(summary)
    if not isinstance(payload, dict):
        return _compact_metric_label_sets(summary)
    rows = payload.get("series")
    if not isinstance(rows, list):
        return []
    labels: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("labels"), dict):
            continue
        raw_labels = row["labels"]
        metric_name = str(raw_labels.get("__name__") or "")
        if metric_name and metric_name not in HSF_METRIC_NAMES:
            continue
        if any(key in raw_labels for key in ("service", "method", "remote_app_name")):
            labels.append(raw_labels)
    return labels


def _compact_metric_label_sets(summary: str) -> list[dict[str, Any]]:
    compact_sets: list[dict[str, str]] = []
    for block in COMPACT_METRIC_BLOCK_RE.findall(summary):
        labels = dict(COMPACT_METRIC_LABEL_RE.findall(block))
        if any(key in labels for key in ("service", "method", "remote_app_name")):
            labels.update(_compact_metric_stats(block))
            compact_sets.append(labels)
    if compact_sets:
        return compact_sets
    label_sets: list[dict[str, str]] = []
    for block in JSON_METRIC_LABEL_BLOCK_RE.findall(summary):
        labels = dict(JSON_METRIC_LABEL_RE.findall(block))
        if any(key in labels for key in ("service", "method", "remote_app_name")):
            label_sets.append(labels)
    if label_sets:
        return label_sets
    labels: dict[str, str] = {}
    for key, value in COMPACT_METRIC_LABEL_RE.findall(summary):
        labels[key] = value
    labels.update(_compact_metric_stats(summary))
    return (
        [labels] if any(key in labels for key in ("service", "method", "remote_app_name")) else []
    )


def _compact_metric_stats(block: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for key in ("min", "max", "avg", "last", "trend"):
        match = re.search(rf"\b{key}=([^,\]\s]+)", block)
        if match:
            output[f"__{key}"] = match.group(1)
    return output


def _hsf_metric_candidate_score(
    item: EvidenceItem,
    labels: dict[str, Any],
    *,
    case_type: str = "",
    alarm_side: str = "",
) -> float:
    score = 5.4 + min(item.score, 1.5)
    metric_name = item.name.lower()
    metric_side = _hsf_metric_side(metric_name)
    trend = str(labels.get("__trend") or "").lower()
    maximum = _float_label(labels, "__max")
    minimum = _float_label(labels, "__min")
    average = _float_label(labels, "__avg")
    last = _float_label(labels, "__last")
    if _all_near_zero(maximum, minimum, average, last):
        score -= 0.75
    if trend == "rising" and any(marker in metric_name for marker in ("error_qps", "_rt")):
        score += 0.12
    if (
        case_type.upper() == "HSF"
        and trend == "rising"
        and "provider_service_method_error_qps" in metric_name
    ):
        score += 0.18
    if trend == "falling" and "success_rate" in metric_name:
        score += 0.12
    if alarm_side and metric_side == alarm_side and "service_method" in metric_name:
        score += 0.25
    elif (
        alarm_side and metric_side and metric_side != alarm_side and "service_method" in metric_name
    ):
        score -= 0.12
    return round(score, 3)


def _hsf_alarm_side(evidence: list[EvidenceItem]) -> str:
    for item in evidence:
        if item.modality != "alarm":
            continue
        text = f"{item.name} {item.summary} {item.command}".lower()
        if "middleware_hsf_consumer" in text or "hsf消费者" in text:
            return "consumer"
        if "middleware_hsf_provider" in text or "hsf提供者" in text:
            return "provider"
    return ""


def _hsf_metric_side(metric_name: str) -> str:
    metric_name = metric_name.lower()
    if "middleware_hsf_consumer" in metric_name:
        return "consumer"
    if "middleware_hsf_provider" in metric_name:
        return "provider"
    return ""


def _hsf_metric_mechanism_props(
    item: EvidenceItem,
    labels: dict[str, Any],
    *,
    case_type: str,
) -> dict[str, Any]:
    metric_name = item.name.lower()
    trend = str(labels.get("__trend") or "").lower()
    maximum = _float_label(labels, "__max")
    average = _float_label(labels, "__avg")
    if (
        case_type.upper() == "HSF"
        and "provider_service_method_error_qps" in metric_name
        and trend == "rising"
        and maximum >= HSF_PROVIDER_LIMIT_MIN_MAX
        and average >= HSF_PROVIDER_LIMIT_MIN_AVG
    ):
        return {
            "failure_mode": "qps_spike_runtime_limit",
            "mechanism": "limit",
            "qps_spike": True,
        }
    return {}


def _float_label(labels: dict[str, Any], key: str) -> float:
    try:
        return float(labels.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _all_near_zero(*values: float) -> bool:
    return all(abs(value) < 1e-9 for value in values)


def _hsf_label(*, app_group: str, service: str, method: str, remote_app: str) -> str:
    service_method = service
    if method and method not in service:
        service_method = f"{service}#{method}" if service else method
    if remote_app:
        return (
            f"{app_group}->{remote_app}:{service_method}"
            if app_group
            else f"{remote_app}:{service_method}"
        )
    return (
        f"{app_group}:{service_method}"
        if app_group and service_method
        else service_method or app_group
    )


def _sql_entity_label(summary: str, features: dict[str, list[str]]) -> str:
    slowest_table = _slowest_sql_table(summary)
    if slowest_table:
        return slowest_table
    if TDDL_TABLE_METRIC_RE.search(summary):
        metric_table_match = METRIC_TABLE_LABEL_RE.search(summary)
        if metric_table_match:
            return metric_table_match.group(1).lower()
    if features.get("sql_tables"):
        return features["sql_tables"][0]
    table_match = SUMMARY_TABLE_RE.search(summary)
    if table_match:
        return table_match.group(1).lower()
    metric_table_match = METRIC_TABLE_LABEL_RE.search(summary)
    if metric_table_match:
        return metric_table_match.group(1).lower()
    for key in ("sql_ids", "sql_dbs", "rds_instances"):
        values = features.get(key) or []
        if values:
            return values[0]
    return ""


def _slowest_sql_table(summary: str) -> str:
    best_table = ""
    best_duration = 0.0
    for match in SQL_SPAN_TABLE_RE.finditer(summary):
        table = match.group("table").strip().strip("`'\"[](){}<>，,.;:").lower()
        if not re.fullmatch(r"[a-z0-9_.$-]{2,80}", table):
            continue
        duration = _max_sql_duration_ms(match.group("tail"))
        if duration > best_duration:
            best_table = table
            best_duration = duration
    return best_table


def _contradictions_for_candidate(
    candidate: dict[str, Any],
    support: list[EvidenceItem],
    all_evidence: list[EvidenceItem],
) -> list[str]:
    kind = str(candidate.get("kind") or "")
    label = str(candidate.get("label") or "")
    props = candidate.get("props") if isinstance(candidate.get("props"), dict) else {}
    reason = str(candidate.get("reason") or "")
    layer = infer_root_layer(kind, label, props, reason)
    support_modalities = {item.modality for item in support} | _modalities_from_candidate(candidate)
    candidate_modality = infer_modality(kind, label, props, reason)
    if candidate_modality != "other":
        support_modalities.add(candidate_modality)
    all_modalities = {item.modality for item in all_evidence}
    contradictions: list[str] = []
    if layer == "database" and "sql" not in support_modalities and "sql" not in all_modalities:
        contradictions.append("database hypothesis has no SQL/RDS evidence in the bundle")
    has_direct_hsf_app_log = kind == "hsf_threadpool_busy" and "log" in support_modalities
    if (
        layer == "service_dependency"
        and not has_direct_hsf_app_log
        and not {"trace", "topology"} & support_modalities
    ):
        contradictions.append(
            "service-dependency hypothesis is not directly backed by trace evidence"
        )
    if kind == "pattern_hsf_threadpool_timeout" and not _has_threadpool_support(
        support, props, reason
    ):
        contradictions.append(
            "HSF threadpool hypothesis has no direct threadpool log or metric evidence"
        )
    if kind == "pattern_infra_event" and "event" in support_modalities:
        return contradictions
    if len(support_modalities - {"other"}) < 2:
        contradictions.append("hypothesis has fewer than two concrete evidence modalities")
    return contradictions


def _has_threadpool_support(
    support: list[EvidenceItem],
    props: dict[str, Any],
    reason: str,
) -> bool:
    if props.get("threadpool_busy") is not True:
        return False
    text = " ".join([reason, *[item.summary for item in support]]).lower()
    return bool(
        re.search(
            r"threadpool_busy|thread pool is full|provider threadpool|hsf[-_ ]?thread|"
            r"hsf线程|线程池(?:打满|满|达到上限|耗尽|饱和)",
            text,
            re.I,
        )
    )


def hypotheses_from_graph(
    graph_context: dict[str, Any],
    evidence: list[EvidenceItem],
    *,
    limit: int = 10,
    support_limit: int = 4,
) -> list[RootHypothesis]:
    """Build ranked ontology hypotheses from graph root candidates."""

    hypotheses: list[RootHypothesis] = []
    seen: set[tuple[str, str]] = set()
    source_candidates = graph_context.get("root_candidates")
    case_type = _case_value(graph_context, "type")
    structured_candidates = [
        *_sql_root_candidates(evidence, case_type=case_type),
        *_hsf_root_candidates(evidence, case_type=case_type),
        *_hsf_app_log_root_candidates(evidence, case_type=case_type),
    ]
    if source_candidates:
        structured_candidates.extend(source_candidates)
    else:
        structured_candidates.extend(topology_root_candidates(graph_context))
        structured_candidates.extend(_fallback_root_candidates(evidence, case_type=case_type))
    raw_candidates = [
        *structured_candidates,
        *_unshadowed_pattern_candidates(
            pattern_root_candidates(_pattern_context(graph_context, evidence)),
            structured_candidates,
        ),
    ]
    direct_business_errors = _direct_business_system_error_labels(evidence)
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "candidate")
        label = str(raw.get("label") or "").strip()
        if not label:
            continue
        key = (kind, label)
        if key in seen:
            continue
        seen.add(key)
        props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
        reason = str(raw.get("reason") or "")
        support = _support_for_candidate(raw, evidence, limit=support_limit)
        modality = infer_modality(kind, label, props, reason)
        modalities = sorted(
            {item.modality for item in support if item.modality != "other"}
            | _modalities_from_candidate(raw)
            | ({modality} if modality != "other" else set())
        )
        root_layer = infer_root_layer(kind, label, props, reason)
        score = _score_from_raw(raw.get("score"))
        score += 0.35 * len(modalities)
        score -= 0.2 * max(0, 2 - len(modalities))
        score += _mechanism_priority_bonus(kind, label, props, reason, case_type=case_type)
        score += _custom_monitor_context_priority_adjustment(
            kind,
            label,
            props,
            reason,
            evidence,
            case_type=case_type,
        )
        score += _hsf_limit_context_priority_adjustment(
            kind,
            evidence,
            case_type=case_type,
        )
        score += _business_system_error_priority_bonus(kind, label, direct_business_errors)
        score += _cache_trace_priority_bonus(kind, label, props, evidence)
        score += _normal_trace_span_penalty(kind, label, props, reason)
        if kind == "event" and OPAQUE_EVENT_LABEL_RE.fullmatch(label):
            score -= 2.0
        if (
            case_type.upper() != "HSF"
            and root_layer == "cache"
            and re.search(r"(?:\btair\b|\bredis\b|\bjedis@|^r-[0-9a-z-]+)", label, re.I)
        ):
            score += 0.7
        if case_type.upper() == "TDDL" and root_layer == "database":
            score += 1.0
        if kind == "log_error" and GENERIC_WRAPPER_EXCEPTION_RE.search(label):
            score -= 0.8
        if label in {"slow_sql", "cache_timeout", "metaq_message_spike", "security_scan"}:
            score -= 0.5
        hypothesis = RootHypothesis(
            id=f"h{len(hypotheses) + 1}",
            kind=kind,
            label=label,
            root_layer=root_layer,
            score=round(score, 3),
            reason=reason,
            entities=entity_features({"label": label, "props": props, "reason": reason}),
            modalities=modalities,
            support=support,
            contradictions=_contradictions_for_candidate(raw, support, evidence),
        )
        hypotheses.append(hypothesis)
    hypotheses.sort(key=lambda item: (-item.score, len(item.contradictions), item.id))
    return hypotheses[:limit]


def _pattern_context(graph_context: dict[str, Any], evidence: list[EvidenceItem]) -> dict[str, Any]:
    context = dict(graph_context)
    context["evidence"] = [
        {"name": item.name, "command": item.command, "summary": item.summary} for item in evidence
    ]
    return context


def _mechanism_priority_bonus(
    kind: str,
    label: str,
    props: dict[str, Any],
    reason: str,
    *,
    case_type: str,
) -> float:
    text = f"{kind} {label} {json.dumps(props, ensure_ascii=False)} {reason}".lower()
    case_type_upper = case_type.upper()
    if kind == "pattern_limit" and any(
        marker in text
        for marker in (
            "blockexception",
            "sentinelblock",
            "ump_sentinel_block",
            "sentinel_block",
            "限流",
        )
    ):
        return 0.95
    if kind == "pattern_security_scan" and ("mtop" in text or case_type_upper == "自定义监控"):
        return 0.45
    if kind == "pattern_security_sql_conflict":
        return 0.75
    if kind == "pattern_notify_business_failure":
        return 0.8
    if kind == "pattern_config_mq_failure":
        return 0.9
    if kind == "pattern_metaq_broker_failure":
        return 1.0
    if kind == "pattern_metaq_duplicate_update_conflict":
        return 0.95
    if kind == "pattern_mq_spike" and case_type_upper == "METAQ":
        return 0.75
    if kind == "pattern_auth_session_failure":
        return 0.95
    if kind == "pattern_app_publish_data_quality":
        return 0.85
    if kind == "pattern_downstream_offline_change":
        return 0.85
    if kind == "metaq_business_failure":
        return 3.8 if case_type_upper == "METAQ" else 0.65
    if kind == "metaq_duplicate_update_conflict":
        return 3.35 if case_type_upper == "METAQ" else 0.75
    if kind == "metaq_broker_failure":
        return 3.3 if case_type_upper in {"METAQ", "HSF"} else 0.75
    if kind == "auth_session_failure":
        return 0.85
    if kind == "pattern_mdm_master_data_missing":
        return 0.8
    if kind == "pattern_tddl_read_traffic_source":
        return 0.85
    if kind == "pattern_infra_event":
        return 0.75
    if kind == "pattern_data_quality":
        return 0.55
    if kind == "pattern_hsf_threadpool_timeout":
        return 0.65
    if kind == "pattern_hsf_downstream_timeout":
        return 0.35
    if kind == "pattern_hsf_provider_subset_rpc_error":
        return 0.45
    if kind == "pattern_tddl_repeated_query_fanout":
        return 0.9
    if kind == "pattern_hsf_provider_error_qps_spike":
        return 0.25
    if kind == "pattern_hsf_cold_start_capacity":
        return 0.7
    if kind == "pattern_connection_pool":
        return 0.9
    if kind == "stale_db_connection":
        return 0.65
    if kind == "hsf_threadpool_busy":
        return 0.9
    if kind == "pattern_cache_timeout" and CONCRETE_CACHE_LABEL_RE.search(label):
        return 0.85
    if kind == "pattern_external_dependency" and re.search(
        r"\b[a-z0-9.-]+\.(?:cn|com|net|org)\b", text
    ):
        return 0.45
    if kind == "pattern_search_dependency" and "igraph" in text:
        return 0.45
    return 0.0


def _custom_monitor_context_priority_adjustment(
    kind: str,
    label: str,
    props: dict[str, Any],
    reason: str,
    evidence: list[EvidenceItem],
    *,
    case_type: str,
) -> float:
    text = f"{kind} {label} {json.dumps(props, ensure_ascii=False)} {reason}".lower()
    case_type_upper = case_type.upper()
    if kind == "custom_monitor_signal" and _has_direct_non_custom_evidence(evidence):
        return -2.4
    if kind == "app_sql_error" and any(
        marker in text
        for marker in (
            "collation",
            "illegal mix",
            "duplicate entry",
            "unique_key",
            "unique key",
            "sqlsyntaxerrorexception",
        )
    ):
        return 1.1
    if (
        case_type_upper == "自定义监控"
        and kind == "pattern_downstream_offline_change"
        and _has_direct_sql_evidence(evidence)
    ):
        return -3.4
    return 0.0


def _hsf_limit_context_priority_adjustment(
    kind: str,
    evidence: list[EvidenceItem],
    *,
    case_type: str,
) -> float:
    if case_type.upper() != "HSF" or kind != "pattern_tddl_repeated_query_fanout":
        return 0.0
    if not _has_direct_limit_evidence(evidence):
        return 0.0
    return -3.0


def _has_direct_limit_evidence(evidence: list[EvidenceItem]) -> bool:
    for item in evidence:
        if _is_empty_observation(item):
            continue
        text = f"{item.name} {item.command} {item.summary}".lower()
        if any(
            marker in text
            for marker in (
                "sentinelblockexception",
                "blockexception",
                "ump_sentinel_block",
                "sentinel_block",
                "限流",
            )
        ):
            return True
    return False


def _has_direct_non_custom_evidence(evidence: list[EvidenceItem]) -> bool:
    for item in evidence:
        if _is_empty_observation(item):
            continue
        if item.name.startswith(("metric_custom_", "monitor_fields_", "monitor_get_", "alarm_get")):
            continue
        if item.name.startswith(("sls_app_", "sls_sql_", "sls_access_", "rds_sql_", "trace_get_")):
            return True
        if item.modality in {"sql", "log", "trace", "event"}:
            return True
    return False


def _has_direct_sql_evidence(evidence: list[EvidenceItem]) -> bool:
    for item in evidence:
        if _is_empty_observation(item):
            continue
        if item.name.startswith(("sls_sql_", "rds_sql_")):
            return True
        if item.modality == "sql" and not item.name.startswith("metric_custom_"):
            return True
    return False


def _direct_business_system_error_labels(evidence: list[EvidenceItem]) -> list[str]:
    labels: list[str] = []
    for item in evidence:
        if not item.name.startswith("sls_app_"):
            continue
        if "kind=business_system_error" not in item.summary:
            continue
        match = BUSINESS_SYSTEM_ERROR_SIGNAL_RE.search(item.summary)
        labels.append(match.group("label") if match else item.summary)
    return labels


def _business_system_error_priority_bonus(kind: str, label: str, direct_labels: list[str]) -> float:
    if not direct_labels:
        return 0.0
    if kind == "business_system_error":
        return 5.1
    has_direct_overlap = _matches_direct_business_error(label, direct_labels)
    if kind == "pattern_data_quality" and has_direct_overlap:
        return 3.5
    if kind == "pattern_downstream_offline_change" and not has_direct_overlap:
        return -4.2
    return 0.0


def _matches_direct_business_error(candidate_label: str, direct_labels: list[str]) -> bool:
    candidate_tokens = _business_error_tokens(candidate_label)
    if not candidate_tokens:
        return False
    for direct_label in direct_labels:
        if candidate_tokens & _business_error_tokens(direct_label):
            return True
    return False


def _business_error_tokens(value: str) -> set[str]:
    normalized = re.sub(r"[.#:~/\\]+", " ", value.lower())
    return {
        token
        for token in BUSINESS_ERROR_TOKEN_RE.findall(normalized)
        if token not in GENERIC_BUSINESS_ERROR_TOKENS
    }


def _cache_trace_priority_bonus(
    kind: str,
    label: str,
    props: dict[str, Any],
    evidence: list[EvidenceItem],
) -> float:
    if kind != "trace_span":
        return 0.0
    if not CONCRETE_CACHE_LABEL_RE.search(label):
        return 0.0
    evidence_text = " ".join(item.summary for item in evidence)
    if not CACHE_TIMEOUT_SIGNAL_RE.search(evidence_text):
        return 0.0
    try:
        evidence_count = float(props.get("evidence_count") or 0)
    except (TypeError, ValueError):
        evidence_count = 0.0
    if evidence_count <= 0:
        return 0.0
    return min(1.65, 0.35 + evidence_count / 12.0)


def _normal_trace_span_penalty(kind: str, label: str, props: dict[str, Any], reason: str) -> float:
    if kind != "trace_span":
        return 0.0
    result_code = str(props.get("result_code") or "").strip().lower()
    if result_code not in {"0", "00", "200", "302", "00/ok", "0/ok"}:
        return 0.0
    try:
        duration_ms = float(props.get("duration_ms") or 0.0)
    except (TypeError, ValueError):
        duration_ms = 0.0
    if duration_ms >= 1000.0:
        return 0.0
    text = f"{label} {json.dumps(props, ensure_ascii=False)} {reason}"
    if CACHE_TIMEOUT_SIGNAL_RE.search(text):
        return 0.0
    try:
        evidence_count = float(props.get("evidence_count") or 0.0)
    except (TypeError, ValueError):
        evidence_count = 0.0
    if evidence_count <= 1.0:
        return -1.35
    return 0.0


def _unshadowed_pattern_candidates(
    pattern_candidates: list[dict[str, Any]],
    structured_candidates: list[Any],
) -> list[dict[str, Any]]:
    structured_kinds = {
        str(item.get("kind") or "") for item in structured_candidates if isinstance(item, dict)
    }
    output: list[dict[str, Any]] = []
    for item in pattern_candidates:
        kind = str(item.get("kind") or "")
        if PATTERN_SHADOW_KINDS.get(kind, set()) & structured_kinds:
            continue
        output.append(item)
    return output


def build_evidence_bundle(
    graph_context: dict[str, Any],
    *,
    evidence_limit: int = 32,
    hypothesis_limit: int = 10,
    support_limit: int = 4,
) -> EvidenceBundle:
    """Convert a raw graph context into a compact RCA evidence bundle."""

    evidence = evidence_items_from_graph(graph_context, limit=evidence_limit)
    hypotheses = hypotheses_from_graph(
        graph_context,
        evidence,
        limit=hypothesis_limit,
        support_limit=support_limit,
    )
    return EvidenceBundle(
        case_id=_case_value(graph_context, "case_id"),
        split=_case_value(graph_context, "split"),
        case_type=_case_value(graph_context, "type") or _case_value(graph_context, "case_type"),
        data_ref=_case_value(graph_context, "data_ref"),
        ontology=_ontology(graph_context),
        retrieval_summary=compact_evidence_summary(
            "retrieval_summary",
            "",
            graph_context.get("retrieval_summary") or "",
        ),
        evidence=evidence,
        hypotheses=hypotheses,
    )


def bundle_prompt_context(bundle: EvidenceBundle, *, hypothesis_limit: int = 5) -> dict[str, Any]:
    """Return the compact context intended for an LLM verifier prompt."""

    return {
        "case_id": bundle.case_id,
        "case_type": bundle.case_type,
        "data_ref": bundle.data_ref,
        "ontology": bundle.ontology,
        "observed_call_paths": [
            item.summary for item in bundle.evidence if item.modality == "topology"
        ][:6],
        "top_hypotheses": [item.to_dict() for item in bundle.hypotheses[:hypothesis_limit]],
    }
