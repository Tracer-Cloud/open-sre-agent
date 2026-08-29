from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

IP_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
TRACE_RE = re.compile(r"\b[0-9a-f]{24,40}\b", re.IGNORECASE)
HSF_PROVIDER_RE = re.compile(r"\[HSF-Provider-/([0-9.]+)\]", re.IGNORECASE)
JAVA_EXCEPTION_RE = re.compile(r"\b(?:[a-zA-Z_$][\w$]*\.)*(?:[A-Z][\w$]*(?:Exception|Error))\b")
ERROR_CODE_RE = re.compile(
    r'"(?:errorCode|exceptionCode)"\s*:\s*"([^"]+)"'
    r"|(?:errorCode|exceptionCode)\s*[:=]\s*([A-Z0-9_.-]+)"
    r"|ERR-CODE:\s*\[([A-Z0-9-]+)\]"
    r"|ex:([A-Z0-9_.-]+)::",
    re.IGNORECASE,
)
SQL_TABLE_RE = re.compile(
    r"\b(?:insert\s+into|update|delete\s+from|from)\s+`?([a-zA-Z0-9_.$-]{2,80})`?",
    re.IGNORECASE,
)
DUPLICATE_RE = re.compile(r"Duplicate entry '([^']+)' for key '([^']+)'", re.IGNORECASE)
METHOD_ERROR_RE = re.compile(r"\b([a-zA-Z_$][\w$]{2,80})\s+error\b", re.IGNORECASE)
UPPER_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_]{3,80}\b")
SERVICE_RE = re.compile(r"\b(?:com|org|net|io|cn)\.[\w.$]+(?::[\w.-]+)?(?:[@#/][\w.$~:-]+)?\b")
DOMAIN_RE = re.compile(r"\b[a-zA-Z0-9.-]+\.(?:cn|com|net|org)\b")
URL_HOST_RE = re.compile(r"https?://([^/\s:]+)", re.IGNORECASE)
REQUEST_URI_RE = re.compile(r'\\?"requestUri\\?"\s*:\s*\\?"([^"\\]+)', re.IGNORECASE)
PAGE_SIZE_RE = re.compile(r'\\?"pageSize\\?"\s*:\s*(\d+)', re.IGNORECASE)
JSON_IN_LIST_RE = re.compile(r'\\?"\$in\\?"\s*:\s*\[(.*?)\]', re.IGNORECASE | re.DOTALL)
MQ_RECV_RE = re.compile(r"\bMQRecv@([^:\s,]+)(?::([^:\s,]+))?", re.IGNORECASE)
MSG_ID_RE = re.compile(r"\bmsgId\s*[=:]\s*([0-9A-Za-z_.:-]{6,120})", re.IGNORECASE)
COUPON_CODE_RE = re.compile(
    r"\bcouponCode\\?\"?\s*[:=]\s*\\?\"?([0-9A-Za-z_-]{4,80})", re.IGNORECASE
)
MAIL_NO_RE = re.compile(r"\\?\"mailNo\\?\"\s*:\s*\\?\"([0-9A-Za-z_-]{4,80})", re.IGNORECASE)
BIZLOG_MAIL_NO_RE = re.compile(r"\|\|[A-Z0-9_]{3,120}_TOPIC\|\|([0-9A-Za-z_-]{4,80})\|\|")
ACTION_RE = re.compile(
    r"\\?\"action\\?\"\s*:\s*\\?\"([A-Z][A-Z0-9_]{1,40})"
    r"|\|\|([A-Z][A-Z0-9_]{1,40})\|\|[A-Z0-9_]{3,120}_TOPIC",
    re.IGNORECASE,
)
MQ_BUSINESS_TAG_RE = re.compile(r"\b(LOAN_DISCOUNT|[A-Z][A-Z0-9_]{3,80})\b")
API_NAME_RE = re.compile(r"\bapi[_-]?name\s*[=:]\s*['\"]?([a-zA-Z0-9_.-]{6,160})", re.IGNORECASE)
LENDER_RE = re.compile(
    r"\b(?:lenderChannelCode|lender|externalOrg|external[_-]?org)\s*[=:]\s*['\"]?([a-zA-Z0-9_-]{2,80})",
    re.IGNORECASE,
)
EXTERNAL_ORG_FAILURE_RE = re.compile(
    r"external org response is not success|external org.*failed|responsecontext[^\n]{0,80}resultcode\s*[=:]\s*failed|resultcode\s*[=:]\s*failed",
    re.IGNORECASE,
)
ROCKETMQ_BROKER_FAILURE_RE = re.compile(
    r"fetch name server address exception|nameserver address exception|name server address exception|"
    r"RemotingConnectException[^\n]{0,160}(?:broker|connect to)|"
    r"MQClientException[^\n]{0,160}broker\[[^\]]+\]|"
    r"broker\[[^\]]+\][^\n]{0,120}(?:not exist|connect|failed|exception)|"
    r"updateConsumeOffsetToBroker|pullKernelImpl|pull message from broker",
    re.IGNORECASE,
)
ROCKETMQ_BROKER_RE = re.compile(r"\bbroker\[([^\]]{2,120})\]", re.IGNORECASE)
ROCKETMQ_TOPIC_RE = re.compile(
    r"\b([A-Za-z0-9][A-Za-z0-9_.:-]{2,120}(?:_metaq|_TOPIC|_topic|Topic|TOPIC)[A-Za-z0-9_.:-]*)\b"
)
AUTH_FAILURE_RE = re.compile(
    r"BucRefreshSsoTokenError|token could not be hit|tenant key error|"
    r"\b(?:statusCode|status|http_status|result_code|resultStr)['\"]?\s*[:=]\s*['\"]?401\b|"
    r"\b401/UNAUTHORIZED\b|\bUNAUTHORIZED\b",
    re.IGNORECASE,
)
AUTH_CONTEXT_RE = re.compile(
    r"\b(?:BUC|SSO|token|login_for_sunfire|tenant key|auth|unauthori[sz]ed)\b|"
    r"认证|鉴权|登录态|登录|tr\.alibaba-inc\.com",
    re.IGNORECASE,
)
HTTP_PATH_RE = re.compile(
    r"\b(?:originalUrl|request_uri|path|url)\s*[:=]\s*['\"]?([^'\"\s,]+)", re.IGNORECASE
)
STALE_DB_CONNECTION_RE = re.compile(
    r"CommunicationsException|Communications link failure|stale connection|last packet successfully received",
    re.IGNORECASE,
)
LAST_PACKET_MS_RE = re.compile(
    r"last packet successfully received from the server was\s+([0-9,]+)\s*millisecon\w*",
    re.IGNORECASE,
)
BUSINESS_SYSTEM_ERROR_RE = re.compile(
    r"(?:ex:)?(?P<code>SYSTEM_ERROR|BIZ_ERROR)::(?P<message>[^\x1e\r\n\"'}]{2,120})",
    re.IGNORECASE,
)
SERVICE_ID_RE = re.compile(
    r"\b(?P<service>(?:com|org|net|io|cn)\.[\w.$]+)#(?P<method>[a-zA-Z_$][\w$]{2,100})\b"
)
PSEUDO_DOMAINS = {"java.net"}
SQL_TABLE_STOPWORDS = {"a", "an", "the", "server", "database", "db", "mysql", "sql"}


@dataclass(frozen=True)
class AppLogSignal:
    """Compact root-cause signal extracted from application SLS logs."""

    kind: str
    label: str
    score: float
    reason: str
    summary: str
    trace_ids: list[str]
    props: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def should_query_app_logs(case_type: str, alarm: dict[str, Any]) -> bool:
    """Return whether generic app SLS logs are likely useful for this alarm."""

    text = _alarm_text(alarm).lower()
    normalized_type = case_type.upper()
    if case_type == "自定义监控" or normalized_type in {"HSF", "OTHER", "JVM", "CPU"}:
        return True
    return any(
        marker in text
        for marker in (
            "keywordmonitor",
            "threadpool",
            "thread pool",
            "hsf",
            "sentinel",
            "errorcode",
            "traceid",
            "sqlid",
            "失败",
            "成功率",
            "线程池",
            "限流",
        )
    )


def rank_app_log_store(store: dict[str, Any]) -> tuple[int, str]:
    """Prefer runtime application logs over audit/monitor/publish stores."""

    logstore = str(store.get("logstore") or store.get("logStore") or "").lower()
    text = " ".join(
        str(store.get(key) or "") for key in ("uni_key", "uniKey", "project", "logstore")
    ).lower()
    if any(
        marker in logstore
        for marker in ("online", "application", "app", "biz", "business", "logtail")
    ):
        return (0, logstore)
    if any(marker in logstore for marker in ("performance", "rtlog", "access", "log")):
        return (1, logstore)
    if any(marker in text for marker in ("online", "application", "app", "business", "logtail")):
        return (2, logstore)
    if any(marker in logstore for marker in ("monitor", "oplog", "audit", "publish", "event")):
        return (4, logstore)
    return (3, logstore)


def app_log_search_queries(alarm: dict[str, Any], *, limit: int = 6) -> list[str]:
    """Build bounded SLS application-log queries from visible alarm fields."""

    text = _alarm_text(alarm)
    lower = text.lower()
    queries: list[str] = []
    if any(marker in lower for marker in ("threadpool", "thread pool", "线程池")):
        queries.extend(["THREADPOOL_BUSY", "thread pool is full", "HSF-Provider"])
    if any(marker in lower for marker in ("metaq", "rocketmq", "mq", "消费成功率", "消费失败")):
        queries.extend(
            [
                "BIZ_ERROR OR BizException OR ConsumeMessageThread",
                "msgId OR MQRecv OR ConsumeMessage",
                "RocketmqCommon OR RemotingConnectException OR MQClientException",
                "broker OR name server OR updateConsumeOffsetToBroker OR pullKernelImpl",
            ]
        )
    if any(marker in lower for marker in ("sql", "sqlid", "数据库", "db")):
        queries.extend(
            [
                "sql_success OR sql_success:false OR sql_success=false",
                "Druid OR connection OR CommunicationsException",
            ]
        )
    if "hsf" in lower:
        queries.extend(
            [
                "HSFException OR THREADPOOL_BUSY OR timeout",
                "HSF-Provider OR HSFServiceAddressNotFoundException",
            ]
        )
        queries.extend(_hsf_business_error_queries(alarm))
    if any(
        marker in lower
        for marker in ("nginx", "后端代理", "login", "sso", "buc", "401", "鉴权", "认证", "登录")
    ):
        queries.extend(
            [
                "BucRefreshSsoTokenError OR tenant key error OR token could not be hit",
                "statusCode: 401 OR UNAUTHORIZED OR login_for_sunfire",
            ]
        )
    if any(marker in lower for marker in ("成功率", "失败", "fail", "error", "custom", "spm")):
        queries.extend(
            [
                "sentinel OR block OR errorCode OR exception OR fail",
                "RuntimeException OR BIZ_ERROR OR MtopException",
                "RocketmqCommon OR RemotingConnectException OR MQClientException",
                "broker OR name server OR updateConsumeOffsetToBroker OR pullKernelImpl",
                "UMP_SENTINEL_BLOCK OR SENTINEL_BLOCK",
            ]
        )
    queries.extend(_important_tokens(text))
    trace_ids = _unique(TRACE_RE.findall(text), 2)
    queries.extend(trace_ids)
    for service in SERVICE_RE.findall(text):
        queries.append(service)
    for value in _tag_values(alarm):
        if _safe_query(value):
            queries.append(value)
    return _unique([query for query in queries if _safe_query(query)], limit)


def summarize_app_logs(records: Any) -> str:
    """Summarize application SLS rows without retaining full stack traces."""

    rows = _sls_rows(records)
    if not rows:
        return "app_logs count=0 top="
    signals = app_log_signals(rows)
    levels = Counter(str(row.get("level") or row.get("LEVEL") or "") for row in rows)
    codes = Counter(code for row in rows for code in _error_codes(_search_text(row)))
    exceptions = Counter(
        exc for row in rows for exc in JAVA_EXCEPTION_RE.findall(_search_text(row))
    )
    trace_ids = _unique([trace for row in rows for trace in _trace_ids(row)], 5)
    sources = _unique([str(row.get("__source__") or row.get("source") or "") for row in rows], 5)
    loggers = Counter(str(row.get("logger") or "") for row in rows)
    return (
        f"app_logs count={len(rows)} levels={_nonempty_counts(levels, 3)} "
        f"error_codes={_nonempty_counts(codes, 5)} exceptions={_nonempty_counts(exceptions, 4)} "
        f"loggers={_nonempty_counts(loggers, 3)} "
        f"top_signals={[signal.summary for signal in signals[:3]]} "
        f"trace_ids={trace_ids} sources={sources}"
    )


def app_log_signals(records: Any) -> list[AppLogSignal]:
    """Extract high-confidence runtime root signals from application SLS rows."""

    rows = _sls_rows(records)
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        detected = _detect_signal(row)
        if detected is None:
            continue
        kind, label = detected
        buckets.setdefault((kind, label), []).append(row)

    signals: list[AppLogSignal] = []
    for (kind, label), items in buckets.items():
        texts = [_search_text(item) for item in items]
        combined = "\n".join(texts[:3])
        trace_ids = _unique([trace for item in items for trace in _trace_ids(item)], 5)
        sources = _unique(
            [str(item.get("__source__") or item.get("source") or "") for item in items], 5
        )
        provider_ips = _unique([ip for text in texts for ip in HSF_PROVIDER_RE.findall(text)], 5)
        error_codes = _unique([code for text in texts for code in _error_codes(text)], 5)
        exceptions = _unique([exc for text in texts for exc in JAVA_EXCEPTION_RE.findall(text)], 5)
        services = _unique([service for text in texts for service in SERVICE_RE.findall(text)], 5)
        business_tags = _unique([tag for text in texts for tag in _mq_business_tags(text)], 5)
        external_orgs = _unique([org for text in texts for org in _external_orgs(text)], 5)
        api_names = _unique([name for text in texts for name in API_NAME_RE.findall(text)], 5)
        broker_names = _unique(
            [name for text in texts for name in ROCKETMQ_BROKER_RE.findall(text)], 8
        )
        broker_ips = _unique([ip for text in texts for ip in _broker_ips(text)], 8)
        topics = _unique([topic for text in texts for topic in _mq_topics(text)], 5)
        http_paths = _unique([path for text in texts for path in _http_paths(text)], 5)
        auth_markers = _unique([marker for text in texts for marker in _auth_markers(text)], 5)
        stale_packet_ms = _unique([value for text in texts for value in _stale_packet_ms(text)], 5)
        sql_id = _first(str(item.get("sql_id") or item.get("sqlId") or "") for item in items)
        table = _sql_table(combined)
        duplicate_value, duplicate_key = _duplicate_parts(combined)
        reason = _reason_for_kind(kind)
        score = _score(kind, len(items), provider_ips, error_codes, sql_id, table, external_orgs)
        summary = (
            f"kind={kind} label={label} count={len(items)} error_codes={error_codes} "
            f"provider_ips={provider_ips} sql_id={sql_id or '-'} table={table or '-'} "
            f"duplicate_key={duplicate_key or '-'} duplicate_value={duplicate_value[:120] or '-'} "
            f"exceptions={exceptions} business_tags={business_tags} external_orgs={external_orgs} "
            f"api_names={api_names} broker_names={broker_names} broker_ips={broker_ips} topics={topics} "
            f"http_paths={http_paths} auth_markers={auth_markers} "
            f"stale_packet_ms={stale_packet_ms} "
            f"services={services} trace_ids={trace_ids} sources={sources}"
        )
        signals.append(
            AppLogSignal(
                kind=kind,
                label=label,
                score=score,
                reason=reason,
                summary=summary,
                trace_ids=trace_ids,
                props={
                    "error_codes": error_codes,
                    "provider_ips": provider_ips,
                    "sql_id": sql_id,
                    "sql_table": table,
                    "duplicate_key": duplicate_key,
                    "duplicate_value": duplicate_value[:160],
                    "exceptions": exceptions,
                    "business_tags": business_tags,
                    "external_orgs": external_orgs,
                    "api_names": api_names,
                    "broker_names": broker_names,
                    "broker_ips": broker_ips,
                    "topics": topics,
                    "http_paths": http_paths,
                    "auth_markers": auth_markers,
                    "stale_packet_ms": stale_packet_ms,
                    "services": services,
                    "sources": sources,
                    "count": len(items),
                },
            )
        )
    signals.sort(key=lambda item: (-item.score, item.kind, item.label))
    return signals


def _detect_signal(row: dict[str, Any]) -> tuple[str, str] | None:
    text = _search_text(row)
    lower = text.lower()
    heavy_query = _heavy_query_parts(text)
    if heavy_query is not None:
        request_uri, page_size, in_list_count = heavy_query
        return "heavy_business_query", (
            f"heavy_query:{request_uri}:pageSize={page_size or '-'}:in={in_list_count}"
        )
    broker_failure = _metaq_broker_failure_label(text)
    if broker_failure:
        return "metaq_broker_failure", broker_failure
    duplicate_conflict = _metaq_duplicate_update_conflict_label(text)
    if duplicate_conflict:
        return "metaq_duplicate_update_conflict", duplicate_conflict
    auth_failure = _auth_failure_label(text)
    if auth_failure:
        return "auth_session_failure", auth_failure
    metaq_failure = _metaq_business_failure_label(text)
    if metaq_failure:
        return "metaq_business_failure", metaq_failure
    business_system_error = _business_system_error_label(row, text)
    if business_system_error:
        return "business_system_error", business_system_error
    if "threadpool_busy" in lower or "thread pool is full" in lower or "线程池满" in lower:
        provider_ip = _first(HSF_PROVIDER_RE.findall(text))
        return "hsf_threadpool_busy", f"THREADPOOL_BUSY:{provider_ip or 'provider'}"
    if _is_external_dependency_failure(text):
        return "external_dependency_failure", _external_dependency_label(text)
    if (
        "ump_sentinel_block" in lower
        or "sentinel_block" in lower
        or "sentinel block" in lower
        or "sentinel限流" in lower
        or "限流" in lower
    ):
        code = (
            _first([item for item in _error_codes(text) if "SENTINEL" in item.upper()])
            or "SENTINEL_BLOCK"
        )
        method = _first(METHOD_ERROR_RE.findall(text))
        return "app_log_limit", f"{code}:{method}" if method else code
    if _is_db_access_failure(row, text):
        if _is_stale_db_connection(text):
            sql_id = str(row.get("sql_id") or row.get("sqlId") or "").strip()
            table = _stale_db_table(text) or _sql_table(text)
            target = table
            if not target and sql_id and sql_id.upper() != "NULL":
                target = sql_id
            return "stale_db_connection", f"stale_jdbc_connection:{target or 'mysql'}"
        if _is_connection_pool_failure(text):
            ip = _first(IP_RE.findall(text))
            return "connection_pool_exhausted", f"connection_pool:{ip or 'db'}"
        if "illegal mix of collations" in lower:
            return "app_sql_error", "data_quality:collation_mismatch"
        sql_id = str(row.get("sql_id") or row.get("sqlId") or "").strip()
        if sql_id and sql_id.upper() != "NULL":
            return "db_access_failure", f"sql_failure:{sql_id}"
        duplicate_value, duplicate_key = _duplicate_parts(text)
        table = _sql_table(text)
        code = _first([item for item in _error_codes(text) if item.upper().startswith("TDDL-")])
        label = ":".join(part for part in (code or "SQL_ERROR", table, duplicate_key) if part)
        return "app_sql_error", label
    if _is_pod_runtime_event(text):
        pod = _first(re.findall(r"\b[a-z0-9][a-z0-9-]{2,80}-[a-z0-9]{5,12}\b", lower))
        return "pod_runtime_event", pod or "pod_runtime_event"
    return None


def _heavy_query_parts(text: str) -> tuple[str, str, int] | None:
    lower = text.lower()
    uri = _first(REQUEST_URI_RE.findall(text))
    page_size = _first(PAGE_SIZE_RE.findall(text))
    in_list_count = _json_in_list_count(text)
    has_heavy_uri = bool(uri) and any(marker in uri.lower() for marker in ("export", "search"))
    has_large_page = page_size.isdigit() and int(page_size) >= 200
    has_large_filter = in_list_count >= 8
    has_business_context = any(
        marker in lower
        for marker in (
            "bigbagwidedetail",
            "inboundbatchcode",
            "warehousecode",
            "mgrprocessor",
            "aggregation",
            "export",
        )
    )
    if has_business_context and has_heavy_uri and (has_large_page or has_large_filter):
        return uri, page_size, in_list_count
    return None


def _metaq_business_failure_label(text: str) -> str:
    lower = text.lower()
    has_mq_context = any(
        marker in lower
        for marker in (
            "metaq",
            "mqrecv",
            "rocketmq",
            "consumemessagethread",
            "consume message",
            "msgid",
        )
    )
    has_business_failure = any(
        marker in lower
        for marker in (
            "biz_error",
            "bizexception",
            "businessexception",
            "consume failed",
            "consume failure",
            "消费失败",
            "未查询到",
            "不存在",
            "not found",
        )
    ) or bool(EXTERNAL_ORG_FAILURE_RE.search(text))
    if not has_mq_context or not has_business_failure:
        return ""

    mq_match = MQ_RECV_RE.search(text)
    topic = mq_match.group(1) if mq_match else ""
    group = mq_match.group(2) if mq_match and mq_match.group(2) else ""
    business_tag = _first(_mq_business_tags(text))
    external_org = _first(_external_orgs(text))
    coupon = _first(COUPON_CODE_RE.findall(text))
    msg_id = _first(MSG_ID_RE.findall(text))
    exception = _first(JAVA_EXCEPTION_RE.findall(text))
    object_label = f"couponCode={coupon}" if coupon else f"msgId={msg_id[:32]}" if msg_id else ""
    root = topic or group or "metaq_message"
    if EXTERNAL_ORG_FAILURE_RE.search(text):
        details = [
            part
            for part in (business_tag, f"lender={external_org}" if external_org else "")
            if part
        ]
        return f"{root}:business_consume_failure" + (":" + ":".join(details[:2]) if details else "")
    details = [part for part in (group, object_label, exception.rsplit(".", 1)[-1]) if part]
    return f"{root}:business_consume_failure" + (":" + ":".join(details[:2]) if details else "")


def _metaq_duplicate_update_conflict_label(text: str) -> str:
    lower = text.lower()
    has_mq_context = any(
        marker in lower
        for marker in (
            "metaq",
            "mqrecv",
            "rocketmq",
            "consumemessagethread",
            "basemetaqlistener",
            "_topic",
        )
    )
    has_update_conflict = (
        "update_error" in lower
        or "updatewithversion" in lower
        or "optimistic" in lower
        or "version conflict" in lower
        or "乐观锁" in text
        or "更新失败" in text
    )
    if not has_mq_context or not has_update_conflict:
        return ""
    topic = _first(_mq_topics(text))
    mail_no = _first([*MAIL_NO_RE.findall(text), *BIZLOG_MAIL_NO_RE.findall(text)])
    action = _first(_action_values(text))
    object_label = f"mailNo={mail_no}" if mail_no else ""
    details = [part for part in (action, object_label) if part]
    root = topic or "metaq_message"
    return f"{root}:duplicate_update_conflict" + (":" + ":".join(details[:2]) if details else "")


def _metaq_broker_failure_label(text: str) -> str:
    lower = text.lower()
    if not ROCKETMQ_BROKER_FAILURE_RE.search(text):
        return ""
    if not any(
        marker in lower
        for marker in ("rocketmq", "metaq", "mqclient", "broker", "nameserver", "name server")
    ):
        return ""
    broker = _first(ROCKETMQ_BROKER_RE.findall(text))
    topic = _first(_mq_topics(text))
    if broker:
        root = broker
    elif topic:
        root = topic
    elif "name server" in lower or "nameserver" in lower:
        root = "rocketmq_name_server"
    else:
        root = "rocketmq_broker"
    return f"{root}:broker_connectivity_failure"


def _mq_business_tags(text: str) -> list[str]:
    if re.search(r"\bLOAN_DISCOUNT\b", text):
        return ["LOAN_DISCOUNT"]
    ignored = {
        "BIZ_ERROR",
        "CHANNEL_GW_10000",
        "CONSUMEMESSAGETHREAD",
        "ERROR",
        "FAILED",
        "HTTP",
        "MQRECV",
        "NULL",
    }
    output: list[str] = []
    for match in MQ_BUSINESS_TAG_RE.finditer(text):
        value = match.group(1)
        if value in ignored or value.startswith("CID_"):
            continue
        output.append(value)
    return _unique(output, 5)


def _action_values(text: str) -> list[str]:
    output: list[str] = []
    for match in ACTION_RE.finditer(text):
        value = match.group(1) or match.group(2) or ""
        if value:
            output.append(value)
    return output


def _external_orgs(text: str) -> list[str]:
    values = [value.lower() for value in LENDER_RE.findall(text)]
    values.extend(re.findall(r"\b([a-z0-9_-]+)\.paylater\.loan\.discount", text.lower()))
    return _unique(values, 5)


def _mq_topics(text: str) -> list[str]:
    ignored_prefixes = ("MQCLIENT", "ROCKETMQ", "MQRECV", "MQSEND")
    values: list[str] = []
    for value in ROCKETMQ_TOPIC_RE.findall(text):
        cleaned = value.strip(" .,:;()[]{}<>\"'")
        if cleaned.upper().startswith(ignored_prefixes):
            continue
        values.append(cleaned)
    return _unique(values, 5)


def _broker_ips(text: str) -> list[str]:
    lower = text.lower()
    if "broker" not in lower and "metaq" not in lower and "rocketmq" not in lower:
        return []
    return _unique(IP_RE.findall(text), 8)


def _auth_failure_label(text: str) -> str:
    if not AUTH_FAILURE_RE.search(text) or not AUTH_CONTEXT_RE.search(text):
        return ""
    lower = text.lower()
    scope = _first(_http_paths(text))
    if not scope:
        service = _first(SERVICE_RE.findall(text))
        scope = service.rsplit(".", 1)[-1] if service else ""
    if not scope and "goc" in lower:
        scope = "goc"
    marker = "buc_sso_token" if _has_buc_sso_context(lower) else "http_401"
    return " ".join(part for part in (scope, marker, "auth_session_failure") if part)


def _has_buc_sso_context(lower_text: str) -> bool:
    return any(
        item in lower_text
        for item in (
            "buc",
            "sso",
            "bucrefreshssotokenerror",
            "token could not be hit",
            "tenant key",
            "login_for_sunfire",
        )
    )


def _http_paths(text: str) -> list[str]:
    paths: list[str] = []
    for raw in HTTP_PATH_RE.findall(text):
        path = raw.split("?", 1)[0].strip(" ,;")
        if path.startswith(("http://", "https://")):
            path_parts = path.split("/", 3)
            path = f"/{path_parts[3]}" if len(path_parts) >= 4 else path
        if path.startswith("/") and len(path) <= 180:
            paths.append(path)
    for raw in re.findall(r"https?://[^/\s]+(/[A-Za-z0-9_./:-]{4,180})", text):
        paths.append(raw.split("?", 1)[0])
    return _unique(paths, 5)


def _auth_markers(text: str) -> list[str]:
    lower = text.lower()
    markers: list[str] = []
    if "bucrefreshssotokenerror" in lower:
        markers.append("BucRefreshSsoTokenError")
    if "token could not be hit" in lower:
        markers.append("token could not be hit")
    if "tenant key error" in lower:
        markers.append("tenant key error")
    if "unauthorized" in lower or re.search(r"\b401\b", text):
        markers.append("401/UNAUTHORIZED")
    if "login_for_sunfire" in lower:
        markers.append("login_for_sunfire")
    return _unique(markers, 5)


def _business_system_error_label(row: dict[str, Any], text: str) -> str:
    match = BUSINESS_SYSTEM_ERROR_RE.search(text)
    if not match:
        return ""
    if not _row_indicates_failure(row, text):
        return ""
    message = _clean_business_message(match.group("message"))
    if not message:
        return ""
    code = match.group("code").upper()
    service_scope = _business_service_scope(row, text)
    return " ".join(part for part in (service_scope, code, message) if part)


def _row_indicates_failure(row: dict[str, Any], text: str) -> bool:
    success = (
        str(row.get("success") or row.get("succeed") or row.get("sql_success") or "")
        .strip()
        .lower()
    )
    if success in {"true", "1", "success", "succeed", "succeeded", "yes"}:
        return False
    if success in {"false", "0", "fail", "failed", "no"}:
        return True
    lower = text.lower()
    return (
        "ex:system_error::" in lower
        or "ex:biz_error::" in lower
        or '"success":false' in lower
        or '"succeed":false' in lower
        or "success=false" in lower
        or "succeed=false" in lower
    )


def _clean_business_message(value: str) -> str:
    cleaned = value.strip().strip(" ,;，。.:：[](){}<>\"'")
    cleaned = re.sub(r"\\[nrttu]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or cleaned.lower() in {"null", "none", "true", "false"}:
        return ""
    return cleaned[:80]


def _business_service_scope(row: dict[str, Any], text: str) -> str:
    service_id = str(row.get("serviceId") or row.get("service_id") or "")
    match = SERVICE_ID_RE.search(service_id) or SERVICE_ID_RE.search(text)
    if not match:
        return ""
    service = match.group("service").rsplit(".", 1)[-1]
    method = match.group("method")
    return f"{service}.{method}"


def _json_in_list_count(text: str) -> int:
    match = JSON_IN_LIST_RE.search(text)
    if not match:
        return 0
    values = re.findall(r'\\?"([^"\\]+)\\?"', match.group(1))
    return len([value for value in values if value])


def _hsf_business_error_queries(alarm: dict[str, Any]) -> list[str]:
    queries = ["SYSTEM_ERROR"]
    for method in _method_query_terms(alarm)[:2]:
        queries.append(f"{method} AND SYSTEM_ERROR")
    return queries


def _method_query_terms(alarm: dict[str, Any]) -> list[str]:
    values: list[str] = []
    values.extend(_tag_values_by_name(alarm, "method"))
    text = _alarm_text(alarm)
    values.extend(
        re.findall(
            r"\b([a-zA-Z_$][\w$]{3,100})~[A-Z]\b",
            text,
        )
    )
    output: list[str] = []
    for value in values:
        method = value.split("~", 1)[0].strip()
        if re.match(r"^[a-zA-Z_$][\w$]{3,100}$", method):
            output.append(method)
    return _unique(output, 3)


def _tag_values_by_name(alarm: dict[str, Any], name: str) -> list[str]:
    output: list[str] = []
    for group in alarm.get("alarm_tags") or []:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "") == name:
                value = str(item.get("value") or "").strip()
                if value:
                    output.append(value)
    return output


def _is_db_access_failure(row: dict[str, Any], text: str) -> bool:
    lower = text.lower()
    sql_success = str(row.get("sql_success") or row.get("sqlSuccess") or "").lower()
    return (
        sql_success == "false"
        or "sql_success=false" in lower
        or "sql_success:false" in lower
        or "tddl-" in lower
        or "err_execute_on_mysql" in lower
        or "duplicate entry" in lower
        or "communicationsexception" in lower
        or "communications link failure" in lower
        or "druiddatasource" in lower
        or "stale connection" in lower
        or "connection pool" in lower
        or "illegal mix of collations" in lower
    )


def _is_external_dependency_failure(text: str) -> bool:
    lower = text.lower()
    if not _candidate_domains(text) and not _remote_ips(text):
        return False
    return any(
        marker in lower
        for marker in (
            "noroutetohostexception",
            "no route to host",
            "unknownhostexception",
            "connecttimeoutexception",
            "sockettimeoutexception",
            "connection reset",
            "connection refused",
            "connection timed out",
            "connect timeout",
            "read timed out",
            "host unreachable",
            "没有到主机的路由",
            "连接超时",
            "连接异常",
            "不可达",
        )
    )


def _external_dependency_label(text: str) -> str:
    domain = _first(_candidate_domains(text))
    if domain:
        return domain
    ip = _first(_remote_ips(text))
    return f"external:{ip}" if ip else "external_dependency"


def _is_connection_pool_failure(text: str) -> bool:
    lower = text.lower()
    pool_marker = any(
        marker in lower
        for marker in (
            "connection pool",
            "druiddatasource",
            "pool exhausted",
            "get connection",
            "连接池",
            "获取连接",
        )
    )
    db_marker = any(
        marker in lower for marker in ("tddl", "jdbc", "mysql", "druid", "datasource", "sql")
    )
    http_client_marker = "poolinghttpclientconnectionmanager" in lower or "apache.http" in lower
    return pool_marker and db_marker and not http_client_marker


def _is_stale_db_connection(text: str) -> bool:
    lower = text.lower()
    if not STALE_DB_CONNECTION_RE.search(text):
        return False
    db_marker = any(
        marker in lower for marker in ("jdbc", "mysql", "tddl", "sql", "datasource", "database")
    )
    return db_marker or "last packet successfully received" in lower


def _stale_db_table(text: str) -> str:
    match = re.search(r"(?:###\s*SQL:|sql\s*[:=])(.{0,700})", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _sql_table(match.group(1))


def _stale_packet_ms(text: str) -> list[str]:
    return [value.replace(",", "") for value in LAST_PACKET_MS_RE.findall(text)]


def _is_pod_runtime_event(text: str) -> bool:
    lower = text.lower()
    return ("pod" in lower or "container" in lower) and any(
        marker in lower
        for marker in ("evict", "oomkilled", "killed", "restart", "unhealthy", "notready")
    )


def _reason_for_kind(kind: str) -> str:
    return {
        "hsf_threadpool_busy": "HSF provider thread pool busy in application log near alarm window",
        "external_dependency_failure": "application log shows downstream external dependency timeout or unreachable connection failure near alarm window",
        "connection_pool_exhausted": "application log shows DB connection pool exhaustion near alarm window",
        "stale_db_connection": "application log shows stale JDBC/MySQL connection failure near alarm window",
        "app_log_limit": "application log shows Sentinel/UMP limiting near alarm window",
        "db_access_failure": "application log shows SQL/DB access failure near alarm window",
        "app_sql_error": "application log shows SQL/TDDL error near alarm window",
        "pod_runtime_event": "application log shows pod/container runtime event near alarm window",
        "heavy_business_query": "application log shows a large export/search request near a resource alarm",
        "metaq_business_failure": "application log shows MetaQ message consumption failed in business handler near alarm window",
        "metaq_duplicate_update_conflict": "application log shows repeated MetaQ consumption hit an update-with-version conflict near alarm window",
        "metaq_broker_failure": "application log shows RocketMQ/MetaQ broker or name-server connectivity failure near alarm window",
        "auth_session_failure": "application log shows BUC/SSO token or HTTP 401 authentication failure near alarm window",
        "business_system_error": "application log shows HSF/business handler returned SYSTEM_ERROR/BIZ_ERROR near alarm window",
    }.get(kind, "application log root signal near alarm window")


def _score(
    kind: str,
    count: int,
    provider_ips: list[str],
    error_codes: list[str],
    sql_id: str,
    table: str,
    external_orgs: list[str] | None = None,
) -> float:
    score = {
        "hsf_threadpool_busy": 4.35,
        "external_dependency_failure": 4.5,
        "connection_pool_exhausted": 4.55,
        "stale_db_connection": 4.65,
        "app_log_limit": 4.45,
        "db_access_failure": 4.0,
        "app_sql_error": 3.75,
        "pod_runtime_event": 4.2,
        "heavy_business_query": 4.3,
        "metaq_business_failure": 4.55,
        "metaq_duplicate_update_conflict": 4.85,
        "metaq_broker_failure": 4.85,
        "auth_session_failure": 4.7,
        "business_system_error": 4.75,
    }.get(kind, 3.5)
    if count >= 3:
        score += 0.25
    if count >= 10:
        score += 0.15
    if provider_ips:
        score += 0.35
    if any("SENTINEL" in code.upper() for code in error_codes):
        score += 0.35
    if any(code.upper().startswith("TDDL-") for code in error_codes):
        score += 0.25
    if sql_id and sql_id.upper() != "NULL":
        score += 0.25
    if table:
        score += 0.15
    if external_orgs:
        score += 0.2
    return round(min(score, 5.0), 3)


def _candidate_domains(text: str) -> list[str]:
    values = [*_unique(URL_HOST_RE.findall(text), 5), *_unique(DOMAIN_RE.findall(text), 8)]
    domains: list[str] = []
    for value in values:
        normalized = value.strip(" .,:;()[]{}<>\"'").lower()
        if not normalized or normalized in PSEUDO_DOMAINS:
            continue
        if normalized in domains:
            continue
        domains.append(normalized)
    return domains


def _remote_ips(text: str) -> list[str]:
    lower = text.lower()
    if "http" not in lower and "remote" not in lower and "host unreachable" not in lower:
        return []
    return _unique(IP_RE.findall(text), 5)


def _alarm_text(alarm: dict[str, Any]) -> str:
    values = [
        str(alarm.get(key) or "") for key in ("metric", "monitor_item_name", "title", "content")
    ]
    values.extend(_tag_values(alarm))
    return " ".join(values)


def _tag_values(alarm: dict[str, Any]) -> list[str]:
    output: list[str] = []
    for group in alarm.get("alarm_tags") or []:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            if value and value.upper() != "NULL":
                output.append(value)
    return output


def _important_tokens(text: str) -> list[str]:
    tokens = [
        token for token in UPPER_TOKEN_RE.findall(text) if token not in {"NULL", "HTTP", "HTTPS"}
    ]
    return _unique(tokens, 4)


def _sls_rows(records: Any) -> list[dict[str, Any]]:
    if isinstance(records, dict):
        raw_rows = records.get("logs") or records.get("items") or records.get("data") or []
    elif isinstance(records, list):
        raw_rows = records
    else:
        raw_rows = []
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        log_item = item.get("logItem") if isinstance(item.get("logItem"), dict) else item
        source_meta = item.get("sourceMeta") if isinstance(item.get("sourceMeta"), dict) else {}
        row = {**source_meta, **log_item}
        if row:
            rows.append(row)
    return rows


def _content(row: dict[str, Any]) -> str:
    if "content" in row:
        return str(row.get("content") or "")
    return " ".join(str(value) for value in row.values() if isinstance(value, str))


def _search_text(row: dict[str, Any]) -> str:
    return f"{_content(row)}\n{json.dumps(row, ensure_ascii=False, default=str)}"


def _error_codes(text: str) -> list[str]:
    output: list[str] = []
    for match in ERROR_CODE_RE.findall(text):
        for value in match:
            if value:
                output.append(value.upper())
    return _unique(output, 8)


def _trace_ids(row: dict[str, Any]) -> list[str]:
    fields = [
        str(row.get("trace") or ""),
        str(row.get("trace_id") or row.get("traceId") or ""),
        _search_text(row),
    ]
    text = "\n".join(fields)
    message_ids = set(MSG_ID_RE.findall(text))
    return _unique([trace for trace in TRACE_RE.findall(text) if trace not in message_ids], 5)


def _sql_table(text: str) -> str:
    match = SQL_TABLE_RE.search(text)
    if not match:
        return ""
    table = match.group(1).strip("`")
    if table.lower() in SQL_TABLE_STOPWORDS:
        return ""
    return table.upper()


def _duplicate_parts(text: str) -> tuple[str, str]:
    match = DUPLICATE_RE.search(text)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def _nonempty_counts(counter: Counter[str], limit: int) -> dict[str, int]:
    return {key: value for key, value in counter.most_common(limit) if key}


def _safe_query(value: str) -> bool:
    return 2 <= len(value) <= 180 and "\n" not in value and "\r" not in value


def _first(values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _unique(values: list[str], limit: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
        if len(output) >= limit:
            break
    return output
