from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.features import infer_modality
from tests.benchmarks.realrca_graph.io import (
    DATASET_DIR,
    DEFAULT_CURRENT_BEST,
    load_cases,
    load_json,
    rows_by_case,
)
from tests.benchmarks.realrca_graph.probe_feedback import ProbeFeedbackLedger, case_suffix

MAX_RAW_TEXT_BYTES = 80_000
HIGH_SIGNAL_FAMILIES = {
    "event",
    "log_error",
    "rds_sql",
    "sls_access",
    "sls_app",
    "sls_sql",
    "trace",
}
FAMILY_MODALITY = {
    "alarm": "alarm",
    "event": "event",
    "log_error": "log",
    "metric": "metric",
    "rds_sql": "sql",
    "sls_access": "log",
    "sls_app": "log",
    "sls_sql": "sql",
    "trace": "trace",
}
MECHANISM_PATTERNS: dict[str, re.Pattern[str]] = {
    "auth_failure": re.compile(
        r"BucRefreshSsoTokenError|token could not be hit|tenant key error|"
        r"\b(?:status|http_status|result(?:_code)?|resultStr)['\"]?\s*[:= ]+\s*['\"]?401\b|"
        r"\b401/UNAUTHORIZED\b|\bUNAUTHORIZED\b",
        re.I,
    ),
    "cache_timeout": re.compile(
        r"(?:tair|redis|jedis).{0,180}(?:timeout|timed out|超时)|"
        r"(?:timeout|timed out|超时).{0,180}(?:tair|redis|jedis)|"
        r"JedisConnectionException|RedisCommandTimeoutException|Tair[A-Za-z]*Timeout",
        re.I | re.DOTALL,
    ),
    "change_event": re.compile(
        r"changefree|event\.query|OFFLINE_HOST|CONFIG_PUSH|deploy|publish|change_id|变更|发布", re.I
    ),
    "connection_pool": re.compile(
        r"connection pool|GetConnectionTimeoutException|stale connection|pool exhausted|maxActive|"
        r"(?:DruidDataSource|Hikari).{0,160}(?:get connection|connection timeout|timeout|"
        r"exhausted|stale|closed|获取连接|连接池)|"
        r"(?:get connection|获取连接).{0,120}(?:timeout|timed out|超时)",
        re.I | re.DOTALL,
    ),
    "data_quality": re.compile(
        r"资格|不存在|未查询到|invalid|empty|null|参数|数据质量|no qualification", re.I
    ),
    "dns_failure": re.compile(r"AddressNotFound|UnknownHostException|\bDNS\b|域名", re.I),
    "duplicate_key": re.compile(r"Duplicate(?: entry)?|unique[_ -]?key|唯一键|duplicate key", re.I),
    "hsf_threadpool_busy": re.compile(
        r"THREADPOOL_BUSY|threadpool[_ -]?busy|HSF-0002|HSFTimeOutException", re.I
    ),
    "http_400": re.compile(
        r"\b(?:status|http[_ -]?code|code)[=: ]+400\b|HTTP/1\.[01]\" 400|Bad Request", re.I
    ),
    "infra_event": re.compile(r"HealthStatus|SystemMaintenance|\bECS\b|宿主机|硬件|机器故障", re.I),
    "jvm_gc": re.compile(r"Full ?GC|OutOfMemory|java heap|JVM|GC overhead", re.I),
    "metaq_broker_failure": re.compile(
        r"RocketmqCommon|fetch name server address exception|"
        r"RemotingConnectException[^\n]{0,160}(?:broker|connect to)|"
        r"MQClientException[^\n]{0,160}broker\[[^\]]+\]|"
        r"broker\[[^\]]+\]|updateConsumeOffsetToBroker|pullKernelImpl",
        re.I,
    ),
    "mq_duplicate_conflict": re.compile(
        r"(?:MetaQ|MQRecv|ConsumeMessageThread|RocketMQ|_TOPIC).{0,240}"
        r"(?:UPDATE_ERROR|updateWithVersion|optimistic(?: lock)?|version conflict|乐观锁|更新失败)"
        r"|(?:UPDATE_ERROR|updateWithVersion|optimistic(?: lock)?|version conflict|乐观锁|更新失败).{0,240}"
        r"(?:MetaQ|MQRecv|ConsumeMessageThread|RocketMQ|_TOPIC)",
        re.I | re.DOTALL,
    ),
    "metaq_business_failure": re.compile(
        r"MetaQ|MQRecv|ConsumeMessageThread|msgId|BizException|BIZ_ERROR", re.I
    ),
    "pod_event": re.compile(
        r"pod[-_ ]?eviction|Evicted|OOMKilled|\bKilling\b|liveness|readiness", re.I
    ),
    "runtime_limit": re.compile(
        r"UMP_SENTINEL_BLOCK|SENTINEL_BLOCK|SentinelBlockException|BlockException|限流", re.I
    ),
    "security_scan": re.compile(
        r"heimdall|SSRF|\bRCE\b|fastjson|攻击|探测|恶意|Fourier|X5Action", re.I
    ),
    "slow_sql": re.compile(
        r"SlowQueries|slow[_ -]?sql|TDDL_QUERY|sql[_ -]?id|SQL_ID|duration[_a-z]*[=: ]+[1-9][0-9]{3,}",
        re.I,
    ),
    "sql_error": re.compile(
        r"SQLException|TDDL-|MySQL|SQLSyntax|DataAccess|数据库|sql exception", re.I
    ),
    "timeout": re.compile(r"\btimeout\b|timed out|超时|HSFTimeOutException", re.I),
}
MECHANISM_HINTS: dict[str, tuple[str, ...]] = {
    "auth_failure": (
        "401",
        "unauthorized",
        "bucrefreshssotokenerror",
        "token",
        "tenant key",
        "login_for_sunfire",
    ),
    "cache_timeout": ("tair", "redis", "jedis", "timeout", "timed out", "超时"),
    "change_event": (
        "changefree",
        "event.query",
        "offline_host",
        "config_push",
        "deploy",
        "publish",
        "change_id",
        "变更",
        "发布",
    ),
    "connection_pool": (
        "connection pool",
        "druiddatasource",
        "hikari",
        "getconnectiontimeoutexception",
        "maxactive",
        "stale connection",
        "pool exhausted",
        "get connection",
        "获取连接",
    ),
    "data_quality": (
        "资格",
        "不存在",
        "未查询到",
        "invalid",
        "empty",
        "null",
        "参数",
        "数据质量",
        "no qualification",
    ),
    "dns_failure": ("addressnotfound", "unknownhostexception", "dns", "域名"),
    "duplicate_key": ("duplicate", "unique", "唯一键"),
    "hsf_threadpool_busy": ("threadpool", "hsf-0002", "hsftimeoutexception"),
    "http_400": ("400", "bad request"),
    "infra_event": ("healthstatus", "systemmaintenance", "ecs", "宿主机", "硬件", "机器故障"),
    "jvm_gc": ("full gc", "outofmemory", "jvm", "gc overhead"),
    "metaq_broker_failure": (
        "rocketmqcommon",
        "name server",
        "remotingconnectexception",
        "broker",
        "updateconsumeoffsettobroker",
        "pullkernelimpl",
    ),
    "mq_duplicate_conflict": (
        "update_error",
        "updatewithversion",
        "optimistic",
        "version conflict",
        "乐观锁",
        "更新失败",
    ),
    "metaq_business_failure": (
        "metaq",
        "mqrecv",
        "consumemessagethread",
        "msgid",
        "bizexception",
        "biz_error",
    ),
    "pod_event": ("pod", "evicted", "oomkilled", "killing", "liveness", "readiness"),
    "runtime_limit": ("sentinel", "blockexception", "限流"),
    "security_scan": (
        "heimdall",
        "ssrf",
        "rce",
        "fastjson",
        "攻击",
        "探测",
        "恶意",
        "fourier",
        "x5action",
    ),
    "slow_sql": ("slow", "tddl_query", "sql_id", "duration"),
    "sql_error": (
        "sqlexception",
        "tddl-",
        "mysql",
        "sqlsyntax",
        "dataaccess",
        "数据库",
        "sql exception",
    ),
    "timeout": ("timeout", "timed out", "超时", "hsftimeoutexception"),
}
TERM_RE = re.compile(
    r"(?:[A-Za-z0-9_.$]+Exception|HSF-\d{4}|THREADPOOL_BUSY|UMP_SENTINEL_BLOCK|"
    r"SENTINEL_BLOCK|BIZ_ERROR|TDDL_[A-Z]+@[^\s\"']+|SQL_ID[=:][^\s\"']+|"
    r"rm-[0-9a-z]+|r-[0-9a-z]+|\b\d{1,3}(?:\.\d{1,3}){3}\b|/[A-Za-z0-9_./:-]{8,})"
)
SQL_MECHANISMS = {"duplicate_key", "slow_sql", "sql_error"}
DATABASE_CASE_TYPES = {"tddl", "rds", "sql", "mysql", "database", "数据库", "慢sql"}
CACHE_CONTEXT_RE = re.compile(
    r"(?:tair|redis|jedis).{0,160}(?:timeout|timed out|超时)|"
    r"(?:timeout|timed out|超时).{0,160}(?:tair|redis|jedis)|"
    r"JedisConnectionException|RedisCommandTimeoutException|Tair[A-Za-z]*Timeout",
    re.I | re.DOTALL,
)
RELATED_APP_EVENT_FILE_RE = re.compile(
    r"event_(?:changefree_query|change_list)_([a-z0-9_]+)\.json$",
    re.I,
)
NODE_HEALTH_EVENT_RE = re.compile(r"^(?:Node\.|Kernel\.OOMKilling)", re.I)
INACTIVE_HEALTH_STATUS = {"false", "normal", "ok", "healthy"}
NORMAL_HEALTH_MESSAGE_RE = re.compile(
    r"nothing oom|load is normal|io is all normal|check is_gpu_node not pass",
    re.I,
)


@dataclass(frozen=True)
class RawFileInventory:
    """One raw artifact and whether its signal is represented in the graph."""

    path: str
    family: str
    byte_count: int
    shape: str
    record_count: int
    nonempty: bool
    referenced_by_graph: bool
    mechanisms: list[str]
    uncovered_mechanisms: list[str]
    sample_terms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawInventoryCase:
    """Raw artifact coverage for one RealRCA case."""

    case_id: str
    case_suffix: str
    case_type: str
    priority: float
    graph_path: str | None
    raw_file_count: int
    nonempty_raw_files: int
    referenced_raw_files: int
    raw_family_counts: dict[str, int]
    graph_modalities: list[str]
    graph_mechanisms: list[str]
    raw_mechanisms: list[str]
    uncovered_mechanisms: list[str]
    categories: list[str]
    recommended_actions: list[str]
    probe_count: int
    best_probe_accuracy: float | None
    top_files: list[RawFileInventory]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["top_files"] = [item.to_dict() for item in self.top_files]
        return payload


@dataclass(frozen=True)
class RawInventoryReport:
    """Aggregate raw artifact inventory used to drive evidence-ingestion work."""

    split: str
    baseline_path: str
    graph_roots: list[str]
    case_count: int
    category_counts: dict[str, int]
    family_counts: dict[str, int]
    mechanism_counts: dict[str, int]
    best_leaderboard_accuracy: float | None
    cases: list[RawInventoryCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "baseline_path": self.baseline_path,
            "graph_roots": list(self.graph_roots),
            "case_count": self.case_count,
            "category_counts": dict(self.category_counts),
            "family_counts": dict(self.family_counts),
            "mechanism_counts": dict(self.mechanism_counts),
            "best_leaderboard_accuracy": self.best_leaderboard_accuracy,
            "cases": [item.to_dict() for item in self.cases],
        }


def build_raw_inventory_report(
    *,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    graph_roots: Sequence[Path] = (),
    split: str = "test",
    dataset_dir: Path = DATASET_DIR,
    case_ids: Sequence[str] = (),
    leaderboard_path: Path | None = None,
    team_name: str = "隐元玩一玩",
    top_files_per_case: int = 8,
) -> RawInventoryReport:
    """Compare raw case artifacts with graph evidence without using hidden references."""

    baseline_rows = rows_by_case(baseline_path)
    dataset_types = _dataset_types(dataset_dir, split)
    target_case_ids = _target_case_ids(baseline_rows.keys(), case_ids)
    ledger = _feedback_ledger(leaderboard_path, team_name)
    cases: list[RawInventoryCase] = []
    for case_id in target_case_ids:
        graph_path = _find_graph_context_path(graph_roots, split, case_id)
        baseline_row = baseline_rows.get(case_id)
        cases.append(
            _inventory_case(
                case_id,
                case_type=dataset_types.get(case_id, ""),
                baseline_text=baseline_row.diagnosis_output if baseline_row is not None else "",
                graph_path=graph_path,
                graph_roots=graph_roots,
                split=split,
                feedback=ledger.for_case_id(case_id) if ledger else None,
                top_files_per_case=top_files_per_case,
            )
        )
    cases.sort(key=lambda item: (-item.priority, item.case_type, item.case_id))
    return RawInventoryReport(
        split=split,
        baseline_path=str(baseline_path),
        graph_roots=[str(path) for path in graph_roots],
        case_count=len(cases),
        category_counts=dict(Counter(category for item in cases for category in item.categories)),
        family_counts=dict(
            Counter(
                family
                for item in cases
                for family, count in item.raw_family_counts.items()
                for _ in range(count)
            )
        ),
        mechanism_counts=dict(
            Counter(mechanism for item in cases for mechanism in item.raw_mechanisms)
        ),
        best_leaderboard_accuracy=ledger.reference_accuracy if ledger else None,
        cases=cases,
    )


def render_raw_inventory_markdown(report: RawInventoryReport, *, limit: int = 50) -> str:
    """Render a compact raw-ingestion gap report."""

    lines = [
        "# RealRCA Raw Evidence Inventory",
        "",
        f"- split: `{report.split}`",
        f"- cases: `{report.case_count}`",
        f"- baseline: `{report.baseline_path}`",
        f"- graph_roots: `{report.graph_roots}`",
        f"- best_leaderboard_accuracy: `{report.best_leaderboard_accuracy}`",
        f"- top_categories: `{_top_counts(report.category_counts)}`",
        f"- top_mechanisms: `{_top_counts(report.mechanism_counts)}`",
        "",
        "## Top Raw Gaps",
        "",
        "| rank | case | type | priority | raw | nonempty | graph_modalities | uncovered | categories | action |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type or "-",
                    f"{item.priority:.2f}",
                    str(item.raw_file_count),
                    str(item.nonempty_raw_files),
                    ",".join(item.graph_modalities) or "-",
                    ",".join(item.uncovered_mechanisms) or "-",
                    ",".join(item.categories[:4]).replace("|", "/") or "-",
                    item.recommended_actions[0].replace("|", "/")
                    if item.recommended_actions
                    else "-",
                ]
            )
            + " |"
        )

    lines.extend(["", "## Case Notes", ""])
    for item in report.cases[:limit]:
        lines.extend(
            [
                f"### `{item.case_suffix}` {item.case_type}",
                "",
                f"- case_id: `{item.case_id}`",
                f"- graph_path: `{item.graph_path}`",
                f"- raw_family_counts: `{item.raw_family_counts}`",
                f"- graph_mechanisms: `{item.graph_mechanisms}`",
                f"- raw_mechanisms: `{item.raw_mechanisms}`",
                f"- uncovered_mechanisms: `{item.uncovered_mechanisms}`",
                f"- probes: count=`{item.probe_count}` best_accuracy=`{item.best_probe_accuracy}`",
                f"- categories: `{item.categories}`",
                f"- recommended_actions: `{item.recommended_actions}`",
                "- top_files:",
            ]
        )
        for raw_file in item.top_files:
            lines.append(
                "  - "
                f"`{Path(raw_file.path).name}` family=`{raw_file.family}` records=`{raw_file.record_count}` "
                f"referenced=`{raw_file.referenced_by_graph}` mechanisms=`{raw_file.mechanisms}` "
                f"uncovered=`{raw_file.uncovered_mechanisms}` terms=`{raw_file.sample_terms[:5]}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _inventory_case(
    case_id: str,
    *,
    case_type: str,
    baseline_text: str,
    graph_path: Path | None,
    graph_roots: Sequence[Path],
    split: str,
    feedback: Any,
    top_files_per_case: int,
) -> RawInventoryCase:
    suffix = case_suffix(case_id)
    if graph_path is None:
        return RawInventoryCase(
            case_id=case_id,
            case_suffix=suffix,
            case_type=case_type,
            priority=10.0,
            graph_path=None,
            raw_file_count=0,
            nonempty_raw_files=0,
            referenced_raw_files=0,
            raw_family_counts={},
            graph_modalities=[],
            graph_mechanisms=[],
            raw_mechanisms=[],
            uncovered_mechanisms=[],
            categories=["missing_graph"],
            recommended_actions=["先重建该 case 的 graph/raw artifact"],
            probe_count=_probe_count(feedback),
            best_probe_accuracy=_best_probe_accuracy(feedback),
            top_files=[],
        )

    graph_context = load_json(graph_path)
    graph_type = str((graph_context.get("case") or {}).get("type") or case_type)
    referenced_paths = _referenced_raw_paths(graph_context)
    graph_modalities = _graph_modalities(graph_context, graph_path, graph_roots, split, case_id)
    graph_mechanisms = sorted(_mechanisms_from_text(_graph_signal_text(graph_context)))
    raw_files = [
        _raw_file_inventory(
            path, graph_mechanisms=graph_mechanisms, referenced_paths=referenced_paths
        )
        for path in sorted((graph_path.parent / "raw").glob("*.json"))
    ]
    raw_files, sidecar_mechanisms = _demote_sidecar_uncovered_mechanisms(
        raw_files,
        case_type=graph_type,
        baseline_text=baseline_text,
        graph_mechanisms=graph_mechanisms,
    )
    raw_files, event_sidecars = _demote_unrelated_event_uncovered_mechanisms(
        raw_files,
        baseline_text=baseline_text,
    )
    sidecar_mechanisms = sorted(set(sidecar_mechanisms) | set(event_sidecars))
    nonempty_files = [item for item in raw_files if item.nonempty]
    family_counts = dict(Counter(item.family for item in raw_files))
    signal_files = [item for item in nonempty_files if item.family in HIGH_SIGNAL_FAMILIES]
    raw_mechanisms = sorted({mechanism for item in signal_files for mechanism in item.mechanisms})
    uncovered_mechanisms = sorted(
        {mechanism for item in signal_files for mechanism in item.uncovered_mechanisms}
    )
    categories = _case_categories(
        nonempty_files,
        graph_modalities=graph_modalities,
        uncovered_mechanisms=uncovered_mechanisms,
        sidecar_mechanisms=sidecar_mechanisms,
        feedback=feedback,
    )
    recommended_actions = _recommended_actions(
        nonempty_files,
        uncovered_mechanisms=uncovered_mechanisms,
        categories=categories,
    )
    top_files = sorted(
        nonempty_files,
        key=lambda item: (
            -len(item.uncovered_mechanisms),
            -(1 if item.family in HIGH_SIGNAL_FAMILIES else 0),
            -item.record_count,
            -item.byte_count,
            item.path,
        ),
    )[:top_files_per_case]
    return RawInventoryCase(
        case_id=case_id,
        case_suffix=suffix,
        case_type=graph_type,
        priority=_priority(nonempty_files, uncovered_mechanisms, categories, feedback),
        graph_path=str(graph_path),
        raw_file_count=len(raw_files),
        nonempty_raw_files=len(nonempty_files),
        referenced_raw_files=sum(1 for item in raw_files if item.referenced_by_graph),
        raw_family_counts=family_counts,
        graph_modalities=graph_modalities,
        graph_mechanisms=graph_mechanisms,
        raw_mechanisms=raw_mechanisms,
        uncovered_mechanisms=uncovered_mechanisms,
        categories=categories,
        recommended_actions=recommended_actions,
        probe_count=_probe_count(feedback),
        best_probe_accuracy=_best_probe_accuracy(feedback),
        top_files=top_files,
    )


def _raw_file_inventory(
    path: Path,
    *,
    graph_mechanisms: Sequence[str],
    referenced_paths: set[str],
) -> RawFileInventory:
    text, payload, shape = _read_raw_payload(path)
    family = _raw_family(path)
    mechanisms = sorted(_mechanisms_from_text(text, family=family))
    mechanisms = _filter_inactive_health_event_mechanisms(
        mechanisms,
        payload=payload,
        family=family,
    )
    nonempty = _is_nonempty_payload(payload, text)
    uncovered = sorted(set(mechanisms) - set(graph_mechanisms)) if nonempty else []
    return RawFileInventory(
        path=str(path),
        family=family,
        byte_count=path.stat().st_size if path.exists() else 0,
        shape=shape,
        record_count=_record_count(payload),
        nonempty=nonempty,
        referenced_by_graph=str(path.resolve()) in referenced_paths,
        mechanisms=mechanisms,
        uncovered_mechanisms=uncovered,
        sample_terms=_sample_terms(text),
    )


def _demote_sidecar_uncovered_mechanisms(
    raw_files: Sequence[RawFileInventory],
    *,
    case_type: str,
    baseline_text: str,
    graph_mechanisms: Sequence[str],
) -> tuple[list[RawFileInventory], list[str]]:
    """Remove raw mechanisms that are only side evidence for an already-explained root."""

    if not _is_cache_case(case_type):
        return list(raw_files), []
    if not _has_cache_explanation(baseline_text, graph_mechanisms):
        return list(raw_files), []

    adjusted: list[RawFileInventory] = []
    demoted: set[str] = set()
    for item in raw_files:
        uncovered = set(item.uncovered_mechanisms)
        sidecar = {
            mechanism
            for mechanism in uncovered
            if mechanism in SQL_MECHANISMS and _is_trace_sidecar_sql(item, mechanism)
        }
        if sidecar:
            demoted.update(sidecar)
            uncovered -= sidecar
            adjusted.append(replace(item, uncovered_mechanisms=sorted(uncovered)))
        else:
            adjusted.append(item)
    return adjusted, sorted(demoted)


def _demote_unrelated_event_uncovered_mechanisms(
    raw_files: Sequence[RawFileInventory],
    *,
    baseline_text: str,
) -> tuple[list[RawFileInventory], list[str]]:
    adjusted: list[RawFileInventory] = []
    demoted: set[str] = set()
    baseline_key = _normalized_app_key(baseline_text)
    for item in raw_files:
        match = RELATED_APP_EVENT_FILE_RE.search(Path(item.path).name)
        if item.family != "event" or match is None:
            adjusted.append(item)
            continue
        app_key = _normalized_app_key(match.group(1))
        if not app_key or app_key in baseline_key:
            adjusted.append(item)
            continue
        sidecar = set(item.uncovered_mechanisms) & {"pod_event"}
        if sidecar:
            demoted.update(sidecar)
            uncovered = sorted(set(item.uncovered_mechanisms) - sidecar)
            adjusted.append(replace(item, uncovered_mechanisms=uncovered))
        else:
            adjusted.append(item)
    return adjusted, sorted(demoted)


def _filter_inactive_health_event_mechanisms(
    mechanisms: Sequence[str],
    *,
    payload: Any,
    family: str,
) -> list[str]:
    mechanism_set = set(mechanisms)
    if family != "event" or not mechanism_set & {"infra_event", "pod_event"}:
        return sorted(mechanism_set)
    if not _inactive_node_health_payload(payload):
        return sorted(mechanism_set)
    return sorted(mechanism_set - {"infra_event", "pod_event"})


def _inactive_node_health_payload(payload: Any) -> bool:
    records = payload if isinstance(payload, list) else [payload]
    considered = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        stream = record.get("stream") if isinstance(record.get("stream"), dict) else {}
        values = record.get("values")
        event_type = str(stream.get("type") or "")
        event_values = values if isinstance(values, list) else []
        if not event_type and event_values:
            value = event_values[0]
            if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], dict):
                event_type = str(value[1].get("type") or "")
        if not NODE_HEALTH_EVENT_RE.search(event_type):
            return False
        for value in event_values or [[None, record]]:
            if not isinstance(value, list) or len(value) < 2 or not isinstance(value[1], dict):
                continue
            event = value[1]
            data = event.get("Data") if isinstance(event.get("Data"), dict) else {}
            status = str(data.get("Status") or event.get("status") or "").strip().lower()
            message = str(data.get("Message") or event.get("message") or "")
            if status not in INACTIVE_HEALTH_STATUS:
                return False
            if message and not NORMAL_HEALTH_MESSAGE_RE.search(message):
                return False
            considered += 1
    return considered > 0


def _is_cache_case(case_type: str) -> bool:
    normalized = case_type.strip().lower()
    return normalized in {"tair", "redis", "cache", "缓存"}


def _has_cache_explanation(baseline_text: str, graph_mechanisms: Sequence[str]) -> bool:
    if "cache_timeout" in set(graph_mechanisms):
        return True
    return bool(CACHE_CONTEXT_RE.search(baseline_text))


def _is_trace_sidecar_sql(item: RawFileInventory, mechanism: str) -> bool:
    if item.family != "trace":
        return False
    if mechanism == "duplicate_key":
        return "Duplicate" not in " ".join(item.sample_terms)
    return True


def _normalized_app_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _read_raw_payload(path: Path) -> tuple[str, Any, str]:
    try:
        raw = path.read_bytes()[:MAX_RAW_TEXT_BYTES]
    except OSError:
        return "", None, "missing"
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text, None, "text"
    if isinstance(payload, list):
        return text, payload, "list"
    if isinstance(payload, dict):
        return text, payload, "object"
    return text, payload, type(payload).__name__


def _record_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in (
            "result",
            "data",
            "items",
            "rows",
            "logs",
            "stores",
            "patterns",
            "messages",
            "values",
        ):
            value = payload.get(key)
            if isinstance(value, list | dict):
                count = _record_count(value)
                if count:
                    return count
        for key in ("count", "total", "totalCount", "size"):
            value = payload.get(key)
            if isinstance(value, int):
                return max(value, 0)
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return sum(1 for value in payload.values() if not _is_empty_value(value))
    return 0


def _is_nonempty_payload(payload: Any, text: str) -> bool:
    if payload is None:
        return bool(text.strip())
    return not _is_empty_value(payload)


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in {"", "[]", "{}"}
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, dict):
        if "result" in value and len(value) <= 2:
            return _is_empty_value(value.get("result"))
        count_values = [value.get(key) for key in ("count", "total", "totalCount", "size")]
        numeric_counts = [item for item in count_values if isinstance(item, int)]
        if numeric_counts and max(numeric_counts) == 0:
            return True
        return all(_is_empty_value(item) for item in value.values())
    return False


def _raw_family(path: Path) -> str:
    name = path.name
    if name.startswith("sls_app_"):
        return "sls_app"
    if name.startswith("sls_sql_"):
        return "sls_sql"
    if name.startswith("sls_access_"):
        return "sls_access"
    if name.startswith("rds_sql_"):
        return "rds_sql"
    if name.startswith("trace_"):
        return "trace"
    if name.startswith("metric_"):
        return "metric"
    if name.startswith("event_"):
        return "event"
    if name.startswith("log_error_"):
        return "log_error"
    if name.startswith("alarm_"):
        return "alarm"
    if name.startswith("app_"):
        return "app"
    if name.startswith("sls_store_"):
        return "sls_store"
    return "other"


def _mechanisms_from_text(text: str, *, family: str | None = None) -> set[str]:
    if not text:
        return set()
    lower_text = text.lower()
    output: set[str] = set()
    for name, pattern in MECHANISM_PATTERNS.items():
        if family is not None and not _mechanism_allowed_for_family(name, family):
            continue
        if not any(hint in lower_text for hint in MECHANISM_HINTS[name]):
            continue
        if pattern.search(text):
            output.add(name)
    return output


def _mechanism_allowed_for_family(mechanism: str, family: str) -> bool:
    if mechanism in {"duplicate_key", "sql_error"}:
        return family in {"log_error", "rds_sql", "sls_app", "sls_sql", "trace"}
    if mechanism == "slow_sql":
        return family in {"log_error", "metric", "rds_sql", "sls_app", "sls_sql", "trace"}
    if mechanism in {"infra_event", "pod_event"}:
        return family in {"event", "log_error", "sls_app"}
    if mechanism == "change_event":
        return family in {"event", "sls_access", "sls_app", "trace"}
    if mechanism in {
        "runtime_limit",
        "hsf_threadpool_busy",
        "connection_pool",
        "jvm_gc",
        "dns_failure",
    }:
        return family in {"log_error", "sls_app", "trace"}
    if mechanism in {"auth_failure", "http_400"}:
        return family in {"sls_access", "sls_app", "trace"}
    if mechanism in {"metaq_business_failure", "metaq_broker_failure", "mq_duplicate_conflict"}:
        return family in {"log_error", "metric", "sls_app", "trace"}
    if mechanism == "cache_timeout":
        return family in {"log_error", "metric", "sls_app", "trace"}
    return family in HIGH_SIGNAL_FAMILIES


def _sample_terms(text: str, *, limit: int = 12) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for match in TERM_RE.finditer(text):
        value = match.group(0).strip().strip('",')
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(value[:120])
        if len(output) >= limit:
            break
    return output


def _graph_signal_text(graph_context: dict[str, Any]) -> str:
    parts: list[Any] = [graph_context.get("retrieval_summary"), graph_context.get("ontology")]
    for key in ("root_candidates", "evidence"):
        values = graph_context.get(key) or []
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    parts.extend(
                        [
                            item.get("kind"),
                            item.get("label"),
                            item.get("reason"),
                            item.get("summary"),
                            item.get("command"),
                            item.get("name"),
                        ]
                    )
    return json.dumps(parts, ensure_ascii=False)[:MAX_RAW_TEXT_BYTES]


def _graph_modalities(
    graph_context: dict[str, Any],
    graph_path: Path,
    graph_roots: Sequence[Path],
    split: str,
    case_id: str,
) -> list[str]:
    modalities: set[str] = set()
    for raw in graph_context.get("evidence") or []:
        if not isinstance(raw, dict):
            continue
        inferred = infer_modality(raw.get("name"), raw.get("command"), raw.get("summary"))
        if inferred != "other":
            modalities.add(inferred)
        family = _raw_family(
            Path(str(raw.get("raw_path") or raw.get("raw_ref") or raw.get("name") or ""))
        )
        modality = FAMILY_MODALITY.get(family)
        if modality:
            modalities.add(modality)
    for raw in graph_context.get("root_candidates") or []:
        if not isinstance(raw, dict):
            continue
        inferred = infer_modality(raw.get("kind"), raw.get("label"), raw.get("reason"))
        if inferred != "other":
            modalities.add(inferred)
    return sorted(modalities)


def _referenced_raw_paths(graph_context: dict[str, Any]) -> set[str]:
    output: set[str] = set()
    for raw in graph_context.get("evidence") or []:
        if not isinstance(raw, dict):
            continue
        for key in ("raw_path", "raw_ref"):
            value = raw.get(key)
            if not value:
                continue
            try:
                output.add(str(Path(str(value)).resolve()))
            except OSError:
                continue
    return output


def _case_categories(
    raw_files: Sequence[RawFileInventory],
    *,
    graph_modalities: Sequence[str],
    uncovered_mechanisms: Sequence[str],
    sidecar_mechanisms: Sequence[str],
    feedback: Any,
) -> list[str]:
    categories: set[str] = set()
    if uncovered_mechanisms:
        categories.add("raw_mechanism_uncovered")
        categories.update(f"raw_mechanism_uncovered:{name}" for name in uncovered_mechanisms)
    for mechanism in sidecar_mechanisms:
        categories.add(f"sidecar_raw_mechanism:{mechanism}")
    for item in raw_files:
        if not item.nonempty or item.family not in HIGH_SIGNAL_FAMILIES:
            continue
        modality = FAMILY_MODALITY.get(item.family)
        if not item.referenced_by_graph:
            categories.add(f"nonempty_raw_not_referenced:{item.family}")
        if modality and modality not in graph_modalities:
            categories.add(f"raw_family_modality_missing:{item.family}->{modality}")
    if feedback and feedback.negative_count:
        categories.add("known_negative_probe")
    return sorted(categories)


def _recommended_actions(
    raw_files: Sequence[RawFileInventory],
    *,
    uncovered_mechanisms: Sequence[str],
    categories: Sequence[str],
) -> list[str]:
    actions: list[str] = []
    if uncovered_mechanisms:
        actions.append("补 raw 解析/ontology 覆盖: " + ",".join(uncovered_mechanisms[:6]))
    not_referenced = sorted(
        {
            item.family
            for item in raw_files
            if item.nonempty
            and item.family in HIGH_SIGNAL_FAMILIES
            and not item.referenced_by_graph
        }
    )
    if not_referenced:
        actions.append("检查非空 raw 是否应进入 graph evidence: " + ",".join(not_referenced[:6]))
    if "known_negative_probe" in categories:
        actions.append("已有负反馈；只在新增证据改变根因边界时再提交")
    if not actions:
        actions.append("raw 和 graph 机制基本对齐；优先做 verifier/root-boundary 分析")
    return actions


def _priority(
    raw_files: Sequence[RawFileInventory],
    uncovered_mechanisms: Sequence[str],
    categories: Sequence[str],
    feedback: Any,
) -> float:
    score = 0.0
    score += len(uncovered_mechanisms) * 2.0
    score += sum(
        1.0
        for item in raw_files
        if item.nonempty and item.family in HIGH_SIGNAL_FAMILIES and not item.referenced_by_graph
    )
    score += sum(
        0.25 for item in raw_files if item.nonempty and item.family in HIGH_SIGNAL_FAMILIES
    )
    if any(category.startswith("raw_family_modality_missing") for category in categories):
        score += 2.0
    if feedback and feedback.negative_count:
        score -= min(4.0, feedback.negative_count * 0.75)
    return round(max(score, 0.0), 3)


def _dataset_types(dataset_dir: Path, split: str) -> dict[str, str]:
    try:
        cases = load_cases(split, dataset_dir)
    except (OSError, json.JSONDecodeError, FileNotFoundError):
        return {}
    return {
        str(item.get("case_id")): str(item.get("type") or "")
        for item in cases
        if isinstance(item, dict) and item.get("case_id")
    }


def _target_case_ids(all_case_ids: Sequence[str] | Any, filters: Sequence[str]) -> list[str]:
    case_ids = [str(item) for item in all_case_ids]
    if not filters:
        return case_ids
    lowered = {item.lower() for item in filters}
    return [
        case_id
        for case_id in case_ids
        if case_id.lower() in lowered or case_suffix(case_id) in lowered
    ]


def _find_graph_context_path(graph_roots: Sequence[Path], split: str, case_id: str) -> Path | None:
    for root in graph_roots:
        path = root / split / case_id / "graph_context.json"
        if path.exists():
            return path
    return None


def _feedback_ledger(leaderboard_path: Path | None, team_name: str) -> ProbeFeedbackLedger | None:
    if not leaderboard_path or not leaderboard_path.exists():
        return None
    payload = load_json(leaderboard_path)
    if not isinstance(payload, dict):
        return None
    return ProbeFeedbackLedger.from_leaderboard(payload, team_name=team_name)


def _probe_count(feedback: Any) -> int:
    return len(feedback.records) if feedback else 0


def _best_probe_accuracy(feedback: Any) -> float | None:
    if not feedback or not feedback.records:
        return None
    return max(record.accuracy for record in feedback.records)


def _top_counts(values: dict[str, int], *, limit: int = 8) -> dict[str, int]:
    return dict(sorted(values.items(), key=lambda item: (-item[1], item[0]))[:limit])
