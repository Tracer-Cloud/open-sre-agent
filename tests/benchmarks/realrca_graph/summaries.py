from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from tests.benchmarks.realrca_graph.access_logs import summarize_access_logs
from tests.benchmarks.realrca_graph.app_logs import summarize_app_logs
from tests.benchmarks.realrca_graph.features import clip_text, text_for_features
from tests.benchmarks.realrca_graph.rds_sql import summarize_rds_sql
from tests.benchmarks.realrca_graph.sql_logs import summarize_sql_logs

LABEL_ORDER = (
    "app_group",
    "remote_app_group",
    "remote_app_name",
    "service",
    "method",
    "server_ip",
    "host_ip",
    "ip",
    "instance",
    "group_id",
    "topic",
    "table",
    "database_name",
    "database_id",
    "db",
)
JAVA_EXCEPTION_RE = re.compile(r"\b(?:[a-zA-Z_$][\w$]*\.)*(?:[A-Z][\w$]*(?:Exception|Error))\b")
TDDL_CODE_RE = re.compile(r"\bTDDL-\d+\b", re.IGNORECASE)
TRACE_RE = re.compile(r"\b[0-9a-f]{24,40}\b", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"\b(?:from|join|update|insert\s+into|delete\s+from)\s+`?([a-zA-Z0-9_.$-]{2,80})`?",
    re.IGNORECASE,
)
TDDL_SPAN_RE = re.compile(
    r"\bTDDL_[A-Z]+@(?P<db>[^\s:\x1a]+)(?::(?P<table>[^\s\x1a]+))?(?:\x1a[0-9a-zA-Z_.$-]+)?",
    re.IGNORECASE,
)
SERVICEISH_RE = re.compile(
    r"\b(?:com|org|net|io|cn)\.[A-Za-z0-9_.$:]+[@#/][\w.$~:-]+\b", re.IGNORECASE
)
MAPPER_RE = re.compile(r"/mybatis/sqlmapper/([^]\s]+?\.xml)", re.IGNORECASE)
ATOM_RE = re.compile(r"\bAtom:([^,\s]+)", re.IGNORECASE)
GROUP_RE = re.compile(r"\bGroup:([^,\s]+)", re.IGNORECASE)
APP_NAME_RE = re.compile(r"\bAppName:([^,\s]+)", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b[a-zA-Z0-9.-]+\.(?:cn|com|net|org)\b")
PSEUDO_DOMAINS = {"java.net"}
ECS_INSTANCE_RE = re.compile(r"\bi-[0-9a-z]{8,}\b", re.IGNORECASE)
ROOT_HINT_RE = re.compile(
    r"Illegal mix of collations|Duplicate entry '[^']+' for key '[^']+'|"
    r"NumberFormatException|BadRequestException|Assert\.notNull|PARAM_ILLEGAL|NO_QUALIFICATION|"
    r"余额不足|不存在|主数据缺失|参数非法|资格|"
    r"Connection reset|Connection refused|Connection timed out|Read timed out|"
    r"THREADPOOL_BUSY|thread pool is full|HSF线程池|线程池(?:打满|满|达到上限)?|"
    r"IGraphServerException|IGraphQueryException|igraph search error|"
    r"SentinelBlockException|BlockException|UMP_SENTINEL_BLOCK|SENTINEL_BLOCK|限流",
    re.IGNORECASE,
)
ROCKETMQ_HINT_RE = re.compile(
    r"fetch name server address exception|name server address exception|nameserver address exception|"
    r"RemotingConnectException[^\n]{0,160}(?:broker|connect to)|"
    r"MQClientException[^\n]{0,160}broker\[[^\]]+\]|"
    r"broker\[[^\]]+\][^\n]{0,120}(?:not exist|connect|failed|exception)|"
    r"updateConsumeOffsetToBroker|pullKernelImpl",
    re.IGNORECASE,
)
BROKER_NAME_RE = re.compile(r"\bbroker\[([^\]]{2,120})\]", re.IGNORECASE)
NOISY_SQL_TABLES = {"ERROR", "THE", "WHERE"}


def _parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _labels(labels: dict[str, Any], *, limit: int = 5) -> str:
    parts: list[str] = []
    for key in LABEL_ORDER:
        value = labels.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
        if len(parts) >= limit:
            break
    if len(parts) < limit:
        for key, value in sorted(labels.items()):
            if key == "__name__" or value in (None, ""):
                continue
            item = f"{key}={value}"
            if item not in parts:
                parts.append(item)
            if len(parts) >= limit:
                break
    return ",".join(parts)


def _summary_stats(summary: dict[str, Any]) -> str:
    parts = []
    for key in ("min", "max", "avg", "last", "trend"):
        value = summary.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={_fmt(value)}")
    return ",".join(parts)


def _series_score(series: dict[str, Any], metric_name: str) -> float:
    summary = series.get("summary") if isinstance(series.get("summary"), dict) else {}
    maximum = float(summary.get("max") or 0.0)
    average = float(summary.get("avg") or 0.0)
    minimum = float(summary.get("min") or 0.0)
    last = float(summary.get("last") or 0.0)
    trend = str(summary.get("trend") or "")
    score = maximum + average + last
    if "success_rate" in metric_name:
        score = (100.0 - min(minimum, last, average)) + maximum * 0.1
    if trend in {"rising", "falling"}:
        score += 3.0
    return score


def _metric_name(name: str, command: str) -> str:
    if name.startswith("metric_"):
        return name.removeprefix("metric_")
    if ")(" in command:
        return command.split(")(", 1)[1].split("{", 1)[0].split(")", 1)[0]
    return name


def _metric_summary(name: str, command: str, payload: dict[str, Any]) -> str:
    metric = _metric_name(name, command)
    series = [item for item in payload.get("series") or [] if isinstance(item, dict)]
    ordered = sorted(series, key=lambda item: _series_score(item, metric), reverse=True)
    top = []
    for item in ordered[:3]:
        labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        top.append(f"[{_labels(labels)} {_summary_stats(summary)}]")
    return clip_text(
        f"metric={metric} series_count={payload.get('series_count', len(series))} top="
        + "; ".join(top),
        700,
    )


def _alarm_summary(payload: dict[str, Any]) -> str:
    tag_parts = []
    for group in payload.get("alarm_tags") or []:
        if not isinstance(group, list):
            continue
        values = []
        for item in group:
            if isinstance(item, dict) and item.get("name") and item.get("value"):
                values.append(f"{item['name']}={item['value']}")
        if values:
            tag_parts.append(",".join(values))
        if len(tag_parts) >= 3:
            break
    return clip_text(
        " ".join(
            part
            for part in (
                f"alarm app={payload.get('app', '')}",
                f"title={payload.get('title', '')}",
                f"metric={payload.get('metric', '')}",
                f"level={payload.get('level', '')}",
                f"time={payload.get('time', '')}",
                f"content={payload.get('content', '')}",
                f"tags={' | '.join(tag_parts)}" if tag_parts else "",
            )
            if part.strip()
        ),
        800,
    )


def _trace_summary(payload: Any) -> str:
    if isinstance(payload, str):
        return clip_text(payload, 700)
    spans = (
        payload
        if isinstance(payload, list)
        else payload.get("spans")
        if isinstance(payload, dict)
        else []
    )
    if not isinstance(spans, list):
        return clip_text(payload, 700)
    typed = [item for item in spans if isinstance(item, dict)]
    ordered = sorted(typed, key=_trace_span_duration_ms, reverse=True)
    top = []
    for item in ordered[:5]:
        top.append(_trace_span_summary(item))
    sql_top = []
    sql_spans = [
        item
        for item in typed
        if "TDDL_" in str(item.get("service") or item.get("serviceDimKey") or "")
    ]
    for item in sorted(sql_spans, key=_trace_span_duration_ms, reverse=True)[:5]:
        sql_top.append(_trace_span_summary(item))
    sql_tables = _trace_sql_table_counts(typed)
    hsf_error_top = _trace_hsf_error_summaries(typed)
    error_top = []
    for item in ordered:
        if not _trace_span_is_error(item):
            continue
        error_top.append(_trace_span_summary(item))
        if len(error_top) >= 5:
            break
    parts = [f"trace spans={len(typed)}"]
    if hsf_error_top:
        parts.append("hsf_error_top=" + "; ".join(hsf_error_top))
    if error_top:
        parts.append("error_top=" + "; ".join(error_top))
    if sql_tables:
        parts.append(f"sql_tables={_nonempty_counts(sql_tables, 5)}")
    if sql_top:
        parts.append("sql_top=" + "; ".join(sql_top))
    parts.append("top=" + "; ".join(top))
    return clip_text(" ".join(parts), 2200)


def _trace_sql_table_counts(spans: list[dict[str, Any]]) -> Counter[str]:
    tables: Counter[str] = Counter()
    for item in spans:
        for key in ("service", "serviceDimKey", "serviceName"):
            value = str(item.get(key) or "")
            match = TDDL_SPAN_RE.search(value)
            if not match:
                continue
            table = str(match.group("table") or "").strip("`'\"[](){}<>，,.;:").lower()
            if table:
                tables[table] += 1
                break
    return tables


def _trace_hsf_error_summaries(spans: list[dict[str, Any]]) -> list[str]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in spans:
        if not _trace_span_is_hsf(item) or not _trace_span_is_error(item):
            continue
        client = str(item.get("clientName") or item.get("client") or "")
        server = str(item.get("serverName") or item.get("server") or "")
        service = str(item.get("serviceName") or item.get("service") or "")
        if not server or not service:
            continue
        key = (client, server, service)
        entry = groups.setdefault(
            key,
            {
                "count": 0,
                "max_duration_ms": 0.0,
                "result_codes": Counter(),
                "provider_ips": Counter(),
                "consumer_ips": Counter(),
            },
        )
        entry["count"] += 1
        entry["max_duration_ms"] = max(
            float(entry["max_duration_ms"]),
            _trace_span_duration_ms(item),
        )
        result = _trace_result_label(item)
        if result:
            entry["result_codes"][result] += 1
        server_ip = _first_text(item, "server_ip", "serverIp")
        host_ip = _first_text(item, "host_ip", "hostIp")
        if server_ip:
            entry["provider_ips"][server_ip] += 1
        if host_ip and host_ip != server_ip:
            entry["consumer_ips"][host_ip] += 1
    ranked = sorted(
        groups.items(),
        key=lambda pair: (
            -int(pair[1]["count"]),
            -float(pair[1]["max_duration_ms"]),
            pair[0],
        ),
    )
    output: list[str] = []
    for (client, server, service), entry in ranked[:5]:
        output.append(
            " ".join(
                part
                for part in (
                    f"client={client}",
                    f"server={server}",
                    f"service={service}",
                    f"failures={entry['count']}",
                    f"max_duration_ms={_fmt_duration_ms(float(entry['max_duration_ms']))}",
                    f"result_codes={_nonempty_counts(entry['result_codes'], 3)}",
                    f"provider_ips={_nonempty_counts(entry['provider_ips'], 5)}",
                    f"consumer_ips={_nonempty_counts(entry['consumer_ips'], 5)}",
                )
                if part and not part.endswith("=")
            )
        )
    return output


def _trace_span_is_hsf(item: dict[str, Any]) -> bool:
    rpc_type = str(item.get("rpcTypeName") or item.get("rpc_type") or "").upper()
    if rpc_type == "HSF":
        return True
    service = str(item.get("serviceName") or item.get("service") or "")
    return bool("@" in service and SERVICEISH_RE.search(service))


def _trace_span_is_error(item: dict[str, Any]) -> bool:
    model = item.get("resultModel") if isinstance(item.get("resultModel"), dict) else {}
    result_name = str(model.get("name") or "").upper()
    result_type = str(model.get("type") or "").upper()
    result_code = str(
        item.get("resultStr")
        or item.get("result_code")
        or item.get("resultType")
        or model.get("code")
        or ""
    )
    if result_name and result_name not in {"OK", "FOUND (302)"}:
        return True
    if result_type and result_type not in {"OK", "SUCCESS"}:
        return True
    return bool(result_code and result_code not in {"0", "00", "200", "302"})


def _trace_result_label(item: dict[str, Any]) -> str:
    model = item.get("resultModel") if isinstance(item.get("resultModel"), dict) else {}
    result = str(item.get("resultStr") or item.get("result_code") or item.get("resultType") or "")
    result_name = str(model.get("name") or "")
    result_type = str(model.get("type") or "")
    parts = [part for part in (result, result_name, result_type) if part]
    output: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = part.upper()
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(part)
    return "/".join(output)


def _trace_span_summary(item: dict[str, Any]) -> str:
    model = item.get("resultModel") if isinstance(item.get("resultModel"), dict) else {}
    result = item.get("resultStr") or item.get("result_code") or item.get("resultType") or ""
    if model.get("name"):
        result = f"{result}/{model.get('name')}"
    result_type = str(model.get("type") or "")
    if result_type and result_type not in {"OK", "SUCCESS"} and result_type not in str(result):
        result = f"{result}/{result_type}"
    server_ip = _first_text(item, "server_ip", "serverIp")
    host_ip = _first_text(item, "host_ip", "hostIp")
    duration_ms = _trace_span_duration_ms(item)
    return " ".join(
        part
        for part in (
            f"client={item.get('clientName') or item.get('client', '')}",
            f"server={item.get('serverName') or item.get('server', '')}",
            f"service={item.get('serviceName') or item.get('service', '')}",
            f"duration_ms={_fmt_duration_ms(duration_ms)}" if duration_ms else "",
            f"result={result}",
            f"server_ip={server_ip}" if server_ip else "",
            f"host_ip={host_ip}" if host_ip and host_ip != server_ip else "",
        )
        if part and not part.endswith("=")
    )


def _trace_span_duration_ms(item: dict[str, Any]) -> float:
    for key in (
        "duration",
        "duration_ms",
        "durationMs",
        "elapsed",
        "cost",
        "spanClient",
        "spanServer",
    ):
        value = _float_field(item.get(key))
        if value > 0:
            return value
    duration_ns = _float_field(item.get("durationNs") or item.get("duration_ns"))
    if duration_ns > 0:
        return duration_ns / 1_000_000.0
    return 0.0


def _float_field(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt_duration_ms(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.4g}"


def _first_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _change_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return clip_text(payload, 700)
    items = []
    for key in ("business_changes", "infra_changes", "changes"):
        raw_items = payload.get(key)
        if isinstance(raw_items, list):
            items.extend(item for item in raw_items if isinstance(item, dict))
    top = []
    for item in sorted(items, key=_change_item_score, reverse=True)[:5]:
        top.append(
            " ".join(
                part
                for part in (
                    f"id={item.get('id') or item.get('change_id') or ''}",
                    f"system={item.get('system') or item.get('change_system') or ''}",
                    f"type={item.get('change_type') or item.get('type') or ''}",
                    f"title={item.get('title') or ''}",
                    f"result={item.get('result') or ''}",
                    f"time={item.get('end_time') or item.get('start_time') or item.get('time') or ''}",
                )
                if part and not part.endswith("=")
            )
        )
    return clip_text(f"changes={len(items)} top=" + "; ".join(top), 800)


def _change_item_score(item: dict[str, Any]) -> tuple[int, int, str]:
    text = text_for_features(item).lower()
    priority = 0
    if "offline_host" in text or "机器下线" in text or "action/res/offline" in text:
        priority += 5
    if "normandy-director" in text:
        priority += 4
    if "变更成功" in text or "success" in text:
        priority += 1
    timestamp = str(item.get("end_time") or item.get("start_time") or item.get("time") or "")
    return priority, len(text), timestamp


def _event_summary(payload: Any) -> str:
    rows = _event_rows(payload)
    if not rows:
        return "events count=0 top="
    records = []
    for row in rows:
        stream = row.get("stream") if isinstance(row.get("stream"), dict) else {}
        value_payloads = _event_value_payloads(row)
        if value_payloads:
            for value in value_payloads:
                records.append((stream, value))
        else:
            records.append((stream, row))
    records.sort(key=_event_record_score, reverse=True)
    top = [_event_record_summary(stream, value) for stream, value in records[:5]]
    return clip_text(
        f"events count={len(records)} top=" + "; ".join(item for item in top if item), 1300
    )


def _event_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("result", "events", "items", "logs", "data"):
            raw_rows = payload.get(key)
            if isinstance(raw_rows, list):
                return [row for row in raw_rows if isinstance(row, dict)]
        return [payload]
    return []


def _event_value_payloads(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = row.get("values")
    output: list[dict[str, Any]] = []
    if not isinstance(values, list):
        return output
    for item in values:
        candidate: Any = item
        timestamp: Any = None
        if isinstance(item, list) and len(item) >= 2:
            timestamp = item[0]
            candidate = item[1]
        if isinstance(candidate, str):
            try:
                decoded = json.loads(candidate)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                candidate = decoded
        if isinstance(candidate, dict):
            payload = dict(candidate)
            if timestamp not in (None, ""):
                payload["__timestamp"] = timestamp
            output.append(payload)
    return output


def _event_record_score(record: tuple[dict[str, Any], dict[str, Any]]) -> float:
    stream, value = record
    text = json.dumps({"stream": stream, "value": value}, ensure_ascii=False).lower()
    score = 0.0
    if "critical" in text:
        score += 4.0
    if "sourceproduct" in text and "ecs" in text:
        score += 2.0
    if "sourceproduct" in text and "schedulerx" in text:
        score += 3.0
    if "changefree" in text or "normandy" in text or "aone" in text:
        score += 3.0
    if "app_publish" in text or "publish" in text or "deploy_id" in text:
        score += 1.5
    if "schedulerx.job" in text:
        score += 1.0
    duration_ms = _range_duration_ms(value)
    if duration_ms >= 10_000:
        score += 1.5
    if "status" in value and str(value.get("status") or "").lower() not in {"", "success"}:
        score += 1.0
    if any(
        marker in text for marker in ("hardware", "memory error", "hostrisk", "systemmaintenance")
    ):
        score += 4.0
    if any(marker in text for marker in ("failure", "insufficientdata", "initializing")):
        score += 1.0
    return score


def _event_record_summary(stream: dict[str, Any], value: dict[str, Any]) -> str:
    data = value.get("data") or value.get("Data") if isinstance(value, dict) else {}
    if not isinstance(data, dict):
        data = {}
    ext_info = _json_object(value.get("ext_info"))
    change_object = _json_object(value.get("change_object"))
    gray_strategy = _json_object(value.get("gray_strategy"))
    change_extra = (
        change_object.get("extraInfo") if isinstance(change_object.get("extraInfo"), dict) else {}
    )
    source_product = _first_nonempty(stream, value, "sourceProduct", "source")
    event_level = _first_nonempty(stream, value, "eventLevel", "level")
    event_type = _first_nonempty(stream, value, "type", "eventType")
    instance_id = _first_instance_id(stream, value, data)
    private_ip = _first_list_value(data.get("privateIpAddress")) or _first_list_value(
        value.get("ip")
    )
    subject = _first_nonempty(stream, value, "subject", "range_instance_id")
    job_status = _first_nonempty(value, data, "status")
    duration_ms = _range_duration_ms(value)
    reason = _first_nonempty(data, value, "reason", "Message", "message")
    alert_rule = _first_nonempty(data, "alertRuleName")
    status = _first_nonempty(data, value, "eventStatus", "healthStatus", "Status") or job_status
    event_id = _first_nonempty(data, value, "eventId", "id")
    event_time = _first_nonempty(
        value, data, "time", "publishTime", "TransitionTime", "__timestamp"
    )
    change_summary = _first_nonempty(value, "change_summary")
    change_system = _first_nonempty(value, "change_system")
    change_type = _first_nonempty(value, "change_type_name")
    change_result = _first_nonempty(value, "change_result")
    deploy_id = _first_nonempty(ext_info, "deploy_id")
    deploy_version = _first_nonempty(change_object, "deploy_version")
    change_app = _first_nonempty(change_object, "appName", "name")
    change_groups = _first_nonempty(change_object, "app_groups", "groups")
    detail_url = _first_nonempty(change_extra, "detailUrl")
    current_batch = _first_nonempty(value, gray_strategy, "current_batch", "currentBatch")
    batch_size = _first_nonempty(value, gray_strategy, "batch_size", "batchSize")
    return " ".join(
        part
        for part in (
            f"sourceProduct={source_product}" if source_product else "",
            f"level={event_level}" if event_level else "",
            f"instanceId={instance_id}" if instance_id else "",
            f"subject={subject}" if subject else "",
            f"type={event_type}" if event_type else "",
            f"change_system={change_system}" if change_system else "",
            f"change_type={change_type}" if change_type else "",
            f"change_result={change_result}" if change_result else "",
            f"change_summary={change_summary}" if change_summary else "",
            f"change_app={change_app}" if change_app else "",
            f"change_groups={change_groups}" if change_groups else "",
            f"deploy_id={deploy_id}" if deploy_id else "",
            f"deploy_version={deploy_version}" if deploy_version else "",
            f"detail_url={detail_url}" if detail_url else "",
            f"batch={current_batch}/{batch_size}" if current_batch and batch_size else "",
            f"duration_ms={duration_ms}" if duration_ms else "",
            f"alertRuleName={alert_rule}" if alert_rule else "",
            f"status={status}" if status else "",
            f"reason={reason}" if reason else "",
            f"privateIp={private_ip}" if private_ip else "",
            f"id={event_id}" if event_id else "",
            f"time={event_time}" if event_time else "",
        )
        if part
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _range_duration_ms(value: dict[str, Any]) -> int:
    try:
        start = int(value.get("range_start_time") or 0)
        end = int(value.get("range_end_time") or 0)
    except (TypeError, ValueError):
        return 0
    if end <= start:
        return 0
    return end - start


def _first_nonempty(*sources: Any) -> str:
    if not sources:
        return ""
    keys = [item for item in sources if isinstance(item, str)]
    containers = [item for item in sources if isinstance(item, dict)]
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value not in (None, ""):
                return _first_list_value(value)
    return ""


def _first_instance_id(*sources: dict[str, Any]) -> str:
    for source in sources:
        for key in ("instanceId", "resourceId", "vmName", "ServerSn", "subject"):
            value = source.get(key)
            if value in (None, ""):
                continue
            if instance := _first_instance_from_value(value):
                return instance
    return ""


def _first_instance_from_value(value: Any) -> str:
    text = _first_list_value(value)
    match = ECS_INSTANCE_RE.search(text)
    return match.group(0) if match else ""


def _first_list_value(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            if item not in (None, ""):
                return str(item)
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return stripped
            return _first_list_value(parsed)
        return stripped
    return "" if value in (None, "") else str(value)


def _app_summary(payload: dict[str, Any]) -> str:
    return clip_text(
        " ".join(
            part
            for part in (
                f"app={payload.get('app', '')}",
                f"health={payload.get('health_status', '')}",
                f"summary={payload.get('summary', '')}",
                f"resources={payload.get('resources', '')}",
            )
            if part.strip()
        ),
        650,
    )


def _log_error_summary(payload: Any) -> str:
    rows = _log_error_rows(payload)
    if not rows:
        return clip_text(payload, 700)
    texts = [_log_error_text(row) for row in rows]
    joined = "\n".join(texts)
    exceptions = Counter(str(row.get("exception") or "") for row in rows if isinstance(row, dict))
    for text in texts:
        exceptions.update(JAVA_EXCEPTION_RE.findall(text))
    codes = Counter(match.upper() for text in texts for match in TDDL_CODE_RE.findall(text))
    tables = Counter(
        _sql_table_name(match) for text in texts for match in SQL_TABLE_RE.findall(text)
    )
    mappers = Counter(MAPPER_RE.findall(joined))
    atoms = Counter(ATOM_RE.findall(joined))
    groups = Counter(GROUP_RE.findall(joined))
    app_names = Counter(APP_NAME_RE.findall(joined))
    domains = Counter(
        domain.lower()
        for domain in DOMAIN_RE.findall(joined)
        if domain.lower() not in PSEUDO_DOMAINS
    )
    root_hints = Counter(match.group(0) for match in ROOT_HINT_RE.finditer(joined))
    broker_hints = Counter(match.group(0) for match in ROCKETMQ_HINT_RE.finditer(joined))
    brokers = Counter(BROKER_NAME_RE.findall(joined))
    trace_ids = _unique([trace for text in texts for trace in TRACE_RE.findall(text)], 5)
    return clip_text(
        " ".join(
            part
            for part in (
                f"log_errors count={len(rows)}",
                f"codes={_nonempty_counts(codes, 4)}",
                f"tables={_nonempty_counts(tables, 5)}",
                f"mappers={_nonempty_counts(mappers, 3)}",
                f"atoms={_nonempty_counts(atoms, 3)}",
                f"groups={_nonempty_counts(groups, 3)}",
                f"app_names={_nonempty_counts(app_names, 3)}",
                f"domains={_nonempty_counts(domains, 3)}",
                f"root_hints={_nonempty_counts(root_hints, 6)}",
                f"broker_hints={_nonempty_counts(broker_hints, 6)}",
                f"brokers={_nonempty_counts(brokers, 6)}",
                f"exceptions={_nonempty_counts(exceptions, 5)}",
                f"trace_ids={trace_ids}",
            )
            if part.strip()
        ),
        1200,
    )


def _log_error_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_rows = payload.get("errors") or payload.get("logs") or payload.get("items") or []
    elif isinstance(payload, list):
        raw_rows = payload
    else:
        raw_rows = []
    return [row for row in raw_rows if isinstance(row, dict)]


def _log_error_text(row: dict[str, Any]) -> str:
    return "\n".join(
        str(row.get(key) or "")
        for key in ("exception", "message", "stack", "next_step", "trace_id", "ip")
    )


def _sql_table_name(value: str) -> str:
    table = value.strip("`").upper()
    return "" if table in NOISY_SQL_TABLES else table


def compact_evidence_summary(name: str, command: str, summary: Any) -> str:
    """Return a compact evidence observation while preserving useful entity names."""

    payload = _parse_jsonish(summary)
    if isinstance(payload, str) and _is_compact_summary(payload):
        return clip_text(payload, 1300)
    lower = f" {name} {command} ".lower()
    if isinstance(payload, dict):
        if name == "alarm_get" or " alarm get " in lower:
            return _alarm_summary(payload)
        if name.startswith("metric_") or " metric " in lower:
            return _metric_summary(name, command, payload)
        if name.startswith("event_") or " event " in lower:
            if name.startswith("event_change_list") or " event change list " in lower:
                return _change_summary(payload)
            return _event_summary(payload)
        if name in {"app_get", "app_resources"}:
            return _app_summary(payload)
        if name == "log_error_list" or " log error list " in lower:
            return _log_error_summary(payload)
        if name.startswith("sls_app_") and isinstance(payload, dict | list):
            return summarize_app_logs(payload)
        if name.startswith("sls_sql_") and isinstance(payload, dict | list):
            return summarize_sql_logs(payload)
        if name.startswith("sls_access_") and isinstance(payload, dict | list):
            return summarize_access_logs(payload)
        if name.startswith("rds_sql_") and isinstance(payload, dict | list):
            return summarize_rds_sql(payload)
    if name.startswith("sls_app_") and isinstance(payload, list):
        return summarize_app_logs(payload)
    if name.startswith("sls_sql_") and isinstance(payload, list):
        return summarize_sql_logs(payload)
    if name.startswith("sls_access_") and isinstance(payload, list):
        return summarize_access_logs(payload)
    if name.startswith("rds_sql_") and isinstance(payload, list):
        return summarize_rds_sql(payload)
    if name.startswith("event_change_list") or " event change list " in lower:
        return _change_summary(payload) if isinstance(payload, dict) else clip_text(payload, 800)
    if name.startswith("event_") or " event " in lower:
        return _event_summary(payload)
    if name.startswith("trace_") or " trace " in lower:
        return _trace_summary(payload)
    return clip_text(payload, 700)


def _is_compact_summary(value: str) -> bool:
    text = value.strip().lower()
    return text.startswith(
        (
            "alarm ",
            "app=",
            "metric=",
            "trace ",
            "trace spans=",
            "events count=",
            "changes=",
            "log_errors count=",
            "app_logs count=",
            "access_logs count=",
            "sql_logs count=",
            "rds_sql count=",
        )
    )


def _nonempty_counts(counter: Counter[str], limit: int) -> dict[str, int]:
    return {key: value for key, value in counter.most_common(limit) if key}


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
