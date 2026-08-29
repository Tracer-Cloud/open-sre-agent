from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

APP_RE = re.compile(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+){1,8}(?:_[a-z0-9]+(?:_[a-z0-9]+)*)?\b")
SERVICE_RE = re.compile(r"\b(?:com|org|net|io|cn)\.[\w.$]+(?::[\w.-]+)?(?:[@#/][\w.$~:-]+)?\b")
JAVA_EXCEPTION_RE = re.compile(r"\b(?:[a-zA-Z_$][\w$]*\.)*(?:[A-Z][\w$]*(?:Exception|Error))\b")
RDS_RE = re.compile(r"\brm-[0-9a-zA-Z-]+\b")
IP_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
TRACE_RE = re.compile(r"\b(?:[0-9a-f]{24,40}|[0-9a-f]{8,16})\b", re.IGNORECASE)
SQL_ID_RE = re.compile(r"\b(?:sql[_-]?id|sqlid)\s*[:=]\s*([0-9a-zA-Z_.$-]{4,80})\b", re.I)
SQL_ENTITY_RE = re.compile(r"^[a-zA-Z0-9_][\w.$-]{1,80}$")
SQL_DML_RE = re.compile(r"\b(?:insert\s+into|update\s+[a-zA-Z0-9_.$-]+|delete\s+from)\b", re.I)
TDDL_TABLE_METRIC_RE = re.compile(r"\bmiddleware_tddl_(?:read|write)_table_", re.I)
TDDL_TABLE_LABEL_RE = re.compile(r"\btable=([a-zA-Z0-9_.$-]{2,80})", re.I)
TDDL_DATABASE_LABEL_RE = re.compile(r"\bdatabase_(?:name|id)=([a-zA-Z0-9_.$-]{2,80})", re.I)
TDDL_SPAN_RE = re.compile(
    r"\b(TDDL_[A-Z]+)@([^\s:\x1a]+)(?::([^\s\x1a]+))?(?:\x1a([0-9a-zA-Z_.$-]{4,80}))?",
    re.I,
)
SQL_SELECT_RE = re.compile(r"\bselect\b.+\bwhere\b", re.I | re.S)
LATIN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.:-]{2,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")

NOISY_TERMS = {
    "alarm",
    "candidate",
    "case",
    "client",
    "current",
    "diagnosis_output",
    "duration",
    "duration_ms",
    "error",
    "evidence",
    "graph",
    "metric",
    "provider",
    "result",
    "result_code",
    "result_type",
    "root",
    "server",
    "service",
    "trace",
    "trace_id",
}

MODALITY_MARKERS = {
    "trace": ("trace", "span", "调用链"),
    "metric": ("metric", "qps", "rt", "success_rate", "指标"),
    "sql": ("sql", "rds", "tddl", "slow", "慢sql", "慢查询", "lock", "锁"),
    "event": ("event", "change", "deploy", "release", "发布", "变更", "重启"),
    "log": ("exception", "access log", "sls", "日志"),
    "alarm": ("alarm", "告警"),
}

KEYWORD_GROUPS = {
    "timeout": (
        "timeout",
        "timed out",
        "hsftimeoutexception",
        "no route to host",
        "host unreachable",
        "request failed",
        "request rejected",
        "超时",
        "下游接口失败",
        "接口失败",
        "请求被拒绝",
        "被拒绝",
    ),
    "limit": (
        "sentinel",
        "sentinel_block",
        "sentinelblockexception",
        "blockexception",
        "ump_sentinel_block",
        "block",
        "rate limit",
        "throttle",
        "tcexception",
        "qps飙升",
        "接口限流",
        "限流",
        "流控",
        "熔断",
    ),
    "sql": ("sql", "rds", "tddl", "慢sql", "慢查询", "lock wait", "锁等待", "数据库表"),
    "repeated_query": (
        "n+1",
        "repeated_query",
        "repeated_sql_fanout",
        "repeat_count",
        "sql fanout",
        "重复查询",
    ),
    "traffic_source": (
        "traffic_source",
        "read_qps_traffic_source",
        "流量来源",
        "上游应用",
        "大量调用",
        "read_qps",
        "读qps",
    ),
    "connection_pool": (
        "connection pool",
        "druiddatasource",
        "druid",
        "tddl_conn",
        "get connection",
        "stale_db_connection",
        "stale jdbc",
        "communications link failure",
        "communicationsexception",
        "last packet",
        "连接池",
        "获取连接",
    ),
    "mq": ("metaq", "rocketmq", "topic", "group_id", "mq", "消息量", "消费量", "堆积", "消费"),
    "consume_failure": (
        "biz_error",
        "business consume failure",
        "consume_failure",
        "notify_receive_success_rate",
        "notify消费成功率",
        "消费失败",
        "业务逻辑异常",
        "handler",
    ),
    "mq_duplicate_conflict": (
        "duplicate_update_conflict",
        "update_error",
        "updatewithversion",
        "optimistic lock",
        "version conflict",
        "重复消费",
        "重复消息",
        "重投",
        "幂等",
        "乐观锁",
        "更新失败",
    ),
    "cache": ("tair", "redis", "cache", "缓存"),
    "auth": (
        "401",
        "unauthorized",
        "auth",
        "buc",
        "sso",
        "token",
        "tenant key",
        "login_for_sunfire",
        "认证",
        "鉴权",
        "登录态",
    ),
    "business_metric": (
        "custom_monitor",
        "custom_monitor_signal",
        "spm_",
        "业务指标",
        "失败数",
        "成功率",
    ),
    "change": ("deploy", "release", "restart", "发布", "变更", "重启", "缩容", "机器下线"),
    "thread_pool": (
        "rejectedexecution",
        "hsf-thread",
        "hsf线程",
        "threadpool",
        "threadpool_busy",
        "thread pool",
        "thread pool is full",
        "线程池",
        "线程池打满",
        "队列满",
    ),
    "provider_rpc_error": ("provider_subset_rpc_error", "rpc_error", "rpc_err", "rpc异常"),
    "memory": (
        "outofmemory",
        "metaspace",
        "fullgc",
        "full gc",
        "fgc",
        "gc overhead",
        "jvm_gc",
        "jvm_memory",
        "内存",
    ),
    "hardware": (
        "hardware",
        "hardware_error",
        "memory error",
        "hostrisk",
        "systemmaintenance",
        "local_disk_nc_down_hardware_error",
        "硬件",
        "宿主机",
        "内存故障",
    ),
    "network": (
        "connection reset",
        "connection refused",
        "connection timed out",
        "connect timeout",
        "network",
        "tcp探测",
        "连接超时",
        "连接异常",
        "不可达",
        "网络",
    ),
    "pod": ("pod", "container", "evict", "oomkilled"),
    "host": (
        "doom_host",
        "doomhost",
        "single-host",
        "target-host",
        "server_ip",
        "_offline_host",
        "_none_core_host",
        "单机",
        "宿主机",
        "节点异常",
        "负载高",
        "冷启动",
        "扩容",
    ),
    "data_quality": (
        "numberformatexception",
        "parselong",
        "illegal mix of collations",
        "collation",
        "duplicate entry",
        "unique key",
        "unique_key",
        "unique-key",
        "badrequestexception",
        "assert.notnull",
        "param_illegal",
        "system_error",
        "business_system_error",
        "电子面单",
        "账户余额不足",
        "no_qualification",
        "write conflict",
        "唯一键",
        "写入冲突",
        "脏数据",
        "字符集",
        "参数非法",
        "主数据缺失",
        "不存在",
        "余额不足",
        "资格",
        "非法",
    ),
    "master_data": (
        "master_data",
        "master_data_missing",
        "mdm_",
        "mdm",
        "主数据",
        "bank not found",
    ),
    "security": (
        "security",
        "security-fourier",
        "fourier",
        "fourier_check",
        "heimdall",
        "x5action",
        "ssrf",
        "rce",
        "fastjson",
        "payload",
        "biztype",
        "malicious",
        "恶意",
        "攻击",
        "安全扫描",
        "路径穿越",
    ),
}


def clip_text(value: Any, limit: int = 700) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else f"{normalized[:limit]}..."


def flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        output: list[str] = []
        for item in value.values():
            output.extend(flatten_strings(item))
        return output
    if isinstance(value, list):
        output = []
        for item in value:
            output.extend(flatten_strings(item))
        return output
    return []


def text_for_features(value: Any) -> str:
    return "\n".join(flatten_strings(value))


def service_base(value: str) -> str:
    return value.split("@", 1)[0].split("#", 1)[0].split("/", 1)[0].strip(" .:$").lower()


def method_from_service(value: str) -> str:
    separator_positions = [
        value.find(separator) for separator in ("@", "#", "/") if separator in value
    ]
    if not separator_positions:
        return ""
    tail = value[min(separator_positions) + 1 :]
    return tail.split("~", 1)[0].split("/", 1)[0].strip().lower()


def _clean_sql_entity(value: str) -> str:
    normalized = value.strip().strip("`'\"[](){}<>，,.;:")
    return normalized.lower() if SQL_ENTITY_RE.fullmatch(normalized) else ""


def _has_concrete_sql_content(text: str) -> bool:
    lower = text.lower()
    return (
        bool(TDDL_SPAN_RE.search(text))
        or bool(TDDL_TABLE_METRIC_RE.search(text))
        or bool(SQL_ID_RE.search(text))
        or bool(SQL_SELECT_RE.search(text))
        or bool(SQL_DML_RE.search(text))
        or any(
            marker in lower
            for marker in (
                "tddl-",
                "err_execute_on_mysql",
                "duplicate entry",
                "communications link failure",
                "query execution was interrupted",
                "慢sql",
                "慢查询",
                "slow sql",
                "slowqueries",
                "full scan",
                "全表扫描",
                "lock wait",
                "锁等待",
            )
        )
    )


def entity_features(value: Any) -> dict[str, list[str]]:
    text = text_for_features(value)
    apps: set[str] = set()
    services: set[str] = set()
    methods: set[str] = set()
    exceptions: set[str] = set()
    rds_instances: set[str] = set()
    ips: set[str] = set()
    traces: set[str] = set()
    sql_ids: set[str] = set()
    sql_ops: set[str] = set()
    sql_dbs: set[str] = set()
    sql_tables: set[str] = set()
    for raw in flatten_strings(value):
        lower = raw.lower()
        apps.update(APP_RE.findall(lower))
        for service in SERVICE_RE.findall(raw):
            base = service_base(service)
            if base:
                services.add(base)
            method = method_from_service(service)
            if method:
                methods.add(method)
        exceptions.update(match.lower() for match in JAVA_EXCEPTION_RE.findall(raw))
        rds_instances.update(match.lower() for match in RDS_RE.findall(raw))
        ips.update(match.lower() for match in IP_RE.findall(raw))
        traces.update(match.lower() for match in TRACE_RE.findall(raw))
        sql_ids.update(match.lower() for match in SQL_ID_RE.findall(raw))
        for op, db, table, sql_hash in TDDL_SPAN_RE.findall(raw):
            sql_ops.add(op.lower())
            if db_entity := _clean_sql_entity(db):
                sql_dbs.add(db_entity)
            if table_entity := _clean_sql_entity(table):
                sql_tables.add(table_entity)
            if sql_hash:
                sql_ids.add(sql_hash.lower())
        if TDDL_TABLE_METRIC_RE.search(raw):
            for table in TDDL_TABLE_LABEL_RE.findall(raw):
                if table_entity := _clean_sql_entity(table):
                    sql_tables.add(table_entity)
            for db in TDDL_DATABASE_LABEL_RE.findall(raw):
                if db_entity := _clean_sql_entity(db):
                    sql_dbs.add(db_entity)
    return {
        "apps": sorted(apps),
        "services": sorted(services),
        "methods": sorted(methods),
        "exceptions": sorted(exceptions),
        "rds_instances": sorted(rds_instances),
        "ips": sorted(ips),
        "traces": sorted(traces),
        "sql_ids": sorted(sql_ids),
        "sql_ops": sorted(sql_ops),
        "sql_dbs": sorted(sql_dbs),
        "sql_tables": sorted(sql_tables),
        "keywords": sorted(keyword_features(text)),
    }


def keyword_features(text: str) -> set[str]:
    lower = text.lower()
    features: set[str] = set()
    for group, needles in KEYWORD_GROUPS.items():
        if any(_keyword_matches(group, lower, needle) for needle in needles):
            features.add(group)
    return features


def _keyword_matches(group: str, lower: str, needle: str) -> bool:
    if group == "security" and needle in {"rce", "ssrf"}:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", lower))
    return needle in lower


@lru_cache(maxsize=8192)
def _token_features_from_strings(strings: tuple[str, ...]) -> frozenset[str]:
    features: set[str] = set()
    for raw in strings:
        lower = raw.lower()
        for app in APP_RE.findall(lower):
            features.add(f"app:{app}")
        for service in SERVICE_RE.findall(raw):
            base = service_base(service)
            if base:
                features.add(f"service:{base}")
            method = method_from_service(service)
            if method:
                features.add(f"method:{method}")
        for exc in JAVA_EXCEPTION_RE.findall(raw):
            features.add(f"exception:{exc.lower()}")
        for rds in RDS_RE.findall(raw):
            features.add(f"rds:{rds.lower()}")
        for ip in IP_RE.findall(raw):
            features.add(f"ip:{ip.lower()}")
        for trace_id in TRACE_RE.findall(raw):
            features.add(f"trace:{trace_id.lower()}")
        for sql_id in SQL_ID_RE.findall(raw):
            features.add(f"sql_id:{sql_id.lower()}")
        for op, db, table, sql_hash in TDDL_SPAN_RE.findall(raw):
            features.add(f"sql_op:{op.lower()}")
            if db_entity := _clean_sql_entity(db):
                features.add(f"sql_db:{db_entity}")
            if table_entity := _clean_sql_entity(table):
                features.add(f"sql_table:{table_entity}")
            if sql_hash:
                features.add(f"sql_id:{sql_hash.lower()}")
        if TDDL_TABLE_METRIC_RE.search(raw):
            for table in TDDL_TABLE_LABEL_RE.findall(raw):
                if table_entity := _clean_sql_entity(table):
                    features.add(f"sql_table:{table_entity}")
            for db in TDDL_DATABASE_LABEL_RE.findall(raw):
                if db_entity := _clean_sql_entity(db):
                    features.add(f"sql_db:{db_entity}")
        for keyword in keyword_features(raw):
            features.add(f"keyword:{keyword}")
        for term in LATIN_RE.findall(lower):
            if (
                len(term) <= 80
                and term not in NOISY_TERMS
                and not term.startswith(("http:", "https:"))
            ):
                features.add(f"term:{term}")
        for value in CJK_RE.findall(raw):
            if 2 <= len(value) <= 12:
                features.add(f"cjk:{value}")
    return frozenset(features)


def token_features(value: Any) -> set[str]:
    return set(_token_features_from_strings(tuple(flatten_strings(value))))


def infer_modality(*values: Any) -> str:
    text = " ".join(text_for_features(value).lower() for value in values if value is not None)
    if "custom_monitor_signal" in text or "custom monitor metric" in text:
        return "metric"
    if "heavy_business_query" in text:
        return "log"
    if "auth_session_failure" in text:
        return "trace"
    if re.search(r"(^|\s)(sf\s+)?alarm\s+get(\s|$)", text) or "alarm_get" in text:
        return "alarm"
    if re.search(r"(^|\s)(sf\s+)?app\s+(get|resources)(\s|$)", text) or "app_get" in text:
        return "other"
    if "app_resources" in text:
        return "other"
    if re.search(r"(^|\s)(sf\s+)?log\s+sls\s+store\s+list(\s|$)", text) or "sls_store_list" in text:
        return "other"
    if re.search(r"(^|\s)(sf\s+)?diagnose\s+rds-sql(\s|$)", text):
        return "sql"
    if "sls_sql_" in text or "sql_logs count=" in text:
        return "sql"
    if TDDL_TABLE_METRIC_RE.search(text):
        return "sql"
    if (
        "sls_access_" in text
        or "access_logs count=" in text
        or "sls_app_" in text
        or "app_logs count=" in text
    ):
        return "log"
    if "pattern_tddl_repeated_query_fanout" in text or "repeated_sql_fanout" in text:
        return "sql"
    if re.search(r"\b(?:trace_get|hsf_error_top|hsf_error)\b", text) and (
        "rpc_error" in text or "timeout" in text or "result_codes=" in text
    ):
        return "trace"
    if "pattern_slow_sql" in text or _has_concrete_sql_content(text):
        return "sql"
    if re.search(r"(^|\s)(sf\s+)?trace\s+", text) or re.search(
        r"\btrace_(?:get|list|stat)\b", text
    ):
        return "trace"
    if re.search(r"(^|\s)(sf\s+)?metric\s+", text) or "metric_" in text:
        return "metric"
    if re.search(r"(^|\s)(sf\s+)?log\s+", text) or "log_" in text:
        return "log"
    if re.search(r"(^|\s)(sf\s+)?event\s+", text) or "event_" in text:
        return "event"
    for modality, markers in MODALITY_MARKERS.items():
        if any(marker in text for marker in markers):
            return modality
    return "other"


def infer_root_layer(kind: str, label: str, props: dict[str, Any], reason: str) -> str:
    text = text_for_features(
        {"kind": kind, "label": label, "props": props, "reason": reason}
    ).lower()
    kind_lower = str(kind or "").lower()
    if kind_lower == "topology_trace_path":
        return "service_dependency"
    if kind_lower == "pattern_mq_spike":
        return "message_queue"
    if kind_lower in {"metaq_broker_failure", "pattern_metaq_broker_failure"}:
        return "message_queue"
    if kind_lower in {"auth_session_failure", "pattern_auth_session_failure"}:
        return "service_dependency"
    if kind_lower == "pattern_notify_business_failure":
        return "application"
    if kind_lower == "custom_monitor_signal":
        return "application"
    if kind_lower in {"metaq_duplicate_update_conflict", "pattern_metaq_duplicate_update_conflict"}:
        return "application"
    if kind_lower == "pattern_config_mq_failure":
        return "change"
    if kind_lower == "pattern_cache_timeout":
        return "cache"
    if kind_lower == "pattern_host_anomaly":
        return "infrastructure"
    if kind_lower == "pattern_infra_event":
        return "infrastructure"
    if kind_lower == "pattern_capacity_change":
        return "change"
    if kind_lower == "pattern_hsf_cold_start_capacity":
        return "change"
    if kind_lower == "pattern_app_publish_data_quality":
        return "change"
    if kind_lower == "pattern_downstream_offline_change":
        return "change"
    if kind_lower == "pattern_instance_count_drop_offline_change":
        return "change"
    if kind_lower == "pattern_slow_sql":
        return "database"
    if kind_lower == "pattern_security_sql_conflict":
        return "database"
    if kind_lower == "pattern_tddl_read_traffic_source":
        return "database"
    if kind_lower == "pattern_connection_pool":
        return "database"
    if kind_lower == "pattern_security_scan":
        return "security"
    if kind_lower == "pattern_limit":
        return "middleware_limit"
    if kind_lower == "pattern_threadpool_busy":
        return "service_dependency"
    if kind_lower in {
        "pattern_hsf_downstream_timeout",
        "pattern_hsf_provider_subset_rpc_error",
        "pattern_hsf_threadpool_timeout",
    }:
        return "service_dependency"
    if kind_lower == "pattern_hsf_provider_error_qps_spike":
        return "service_dependency"
    if kind_lower in {"pattern_jvm_gc_pressure", "pattern_jvm_memory"}:
        return "infrastructure"
    if kind_lower == "pattern_external_dependency":
        return "service_dependency"
    if kind_lower == "pattern_search_dependency":
        return "service_dependency"
    if kind_lower == "pattern_data_quality":
        return "application"
    if kind_lower == "pattern_mdm_master_data_missing":
        return "application"
    if kind_lower == "pattern_schedulerx_batch_load":
        return "application"
    if kind_lower in {"hsf_service_method", "hsf_threadpool_busy", "topology_trace_path"}:
        return "service_dependency"
    if kind_lower in {"app_log_limit"}:
        return "middleware_limit"
    if kind_lower in {"external_dependency_failure"}:
        return "service_dependency"
    if kind_lower in {
        "heavy_business_query",
        "business_system_error",
        "metaq_business_failure",
        "metaq_duplicate_update_conflict",
    }:
        return "application"
    if kind_lower in {
        "connection_pool_exhausted",
        "stale_db_connection",
        "db_access_failure",
        "app_sql_error",
        "evidence_sql",
        "pattern_tddl_repeated_query_fanout",
        "sql_log_error",
        "rds_sql_stat",
        "rds_sql_detail",
    }:
        return "database"
    if kind_lower in {"pod_runtime_event"}:
        return "infrastructure"
    if any(marker in text for marker in ("metaq", "rocketmq", "topic")):
        return "message_queue"
    if any(marker in text for marker in ("tair", "redis", "jedis", "cache")):
        return "cache"
    if any(marker in text for marker in ("sql", "rds", "tddl", "rm-")):
        return "database"
    if any(marker in text for marker in ("sentinel", "throttle", "限流", "熔断")):
        return "middleware_limit"
    if any(marker in text for marker in ("connection pool", "druid", "jdbc")):
        return "database"
    if "change" in text or "deploy" in text or "发布" in text or "变更" in text:
        return "change"
    if any(marker in text for marker in ("threadpool_busy", "thread pool is full", "线程池满")):
        return "service_dependency"
    if any(marker in text for marker in ("pod", "container", "evict", "oomkilled")):
        return "infrastructure"
    if "provider" in text or "server" in text or "trace_span" in kind:
        return "service_dependency"
    if "host" in text or "ip" in text or "ecs" in text:
        return "infrastructure"
    return "application"
