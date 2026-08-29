from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

IP_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
TRACE_RE = re.compile(r"\b[0-9a-f]{24,40}\b", re.IGNORECASE)
EAGLEEYE_RE = re.compile(r"\beagleEyeId=([0-9a-f]{24,40})\b", re.IGNORECASE)
TDDL_CODE_RE = re.compile(r"\bTDDL-\d+\b", re.IGNORECASE)
SQL_TABLE_RE = re.compile(
    r"\b(?:insert\s+into|update|delete\s+from|from)\s+`?([a-zA-Z0-9_.$-]{2,80})`?",
    re.IGNORECASE,
)
GROUP_RE = re.compile(r"\bGROUP\s+'([^']+)'", re.IGNORECASE)
ATOM_RE = re.compile(r"\bATOM\s+'([^']+)'", re.IGNORECASE)
DUPLICATE_RE = re.compile(r"Duplicate entry '([^']+)' for key '([^']+)'", re.IGNORECASE)
JAVA_EXCEPTION_RE = re.compile(r"\b(?:[a-zA-Z_$][\w$]*\.)*(?:[A-Z][\w$]*(?:Exception|Error))\b")
SQL_TABLE_STOPWORDS = {"a", "an", "the", "server", "database", "db", "mysql", "sql"}


@dataclass(frozen=True)
class SqlLogSignal:
    """Compact root-cause signal extracted from business SLS SQL logs."""

    label: str
    score: float
    reason: str
    summary: str
    trace_ids: list[str]
    props: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def should_query_sql_logs(case_type: str, alarm: dict[str, Any]) -> bool:
    """Return whether app SLS logs are likely useful for SQL/TDDL evidence."""

    text = " ".join(
        str(alarm.get(key) or "") for key in ("metric", "monitor_item_name", "title", "content")
    ).lower()
    if case_type.upper() == "TDDL":
        return True
    return any(marker in text for marker in ("tddl", "sql", "mysql", "rds", "数据库", "慢sql"))


def rank_sql_log_store(store: dict[str, Any]) -> tuple[int, str]:
    """Prefer application log stores over monitor/op/audit stores."""

    logstore = str(store.get("logstore") or store.get("logStore") or "").lower()
    text = " ".join(
        str(store.get(key) or "") for key in ("uni_key", "uniKey", "project", "logstore")
    ).lower()
    if any(marker in logstore for marker in ("logtail", "application", "app", "error")):
        return (0, logstore)
    if any(marker in text for marker in ("logtail", "application", "app", "error")):
        return (1, logstore)
    if any(marker in logstore for marker in ("monitor", "oplog", "audit")):
        return (3, logstore)
    return (2, logstore)


def sql_log_search_queries(alarm: dict[str, Any], *, limit: int = 4) -> list[str]:
    """Build bounded SLS queries for SQL/TDDL failures from visible alarm fields."""

    text = " ".join(
        str(alarm.get(key) or "") for key in ("metric", "monitor_item_name", "title", "content")
    )
    ips = _unique(IP_RE.findall(text) + _tag_values(alarm, "ip") + _tag_values(alarm, "host_ip"), 3)
    roots = [
        "TDDL-4614",
        "ERR_EXECUTE_ON_MYSQL",
        "Duplicate entry",
        "Communications link failure",
        "Query execution was interrupted",
    ]
    queries: list[str] = []
    for ip in ips:
        queries.append(f"{ip} AND (Duplicate OR deadlock OR timeout OR SQL OR connection OR pool)")
        queries.append(f"{ip} AND TDDL-4614")
        queries.append(f"{ip} AND (ERR_EXECUTE_ON_MYSQL OR Communications OR interrupted)")
    queries.extend(roots)
    return _unique(queries, limit)


def summarize_sql_logs(records: Any) -> str:
    """Summarize SLS SQL log rows without retaining full stack traces."""

    rows = _sls_rows(records)
    if not rows:
        return "sql_logs count=0 top="
    signals = sql_log_signals(rows)
    codes = Counter(_first(TDDL_CODE_RE.findall(_content(row))).upper() for row in rows)
    tables = Counter(_sql_table(_content(row)) for row in rows)
    exceptions = Counter(_first(JAVA_EXCEPTION_RE.findall(_content(row))) for row in rows)
    traces = _unique([trace for row in rows for trace in _trace_ids(_content(row))], 5)
    sources = _unique([str(row.get("__source__") or row.get("source") or "") for row in rows], 5)
    return (
        f"sql_logs count={len(rows)} codes={_nonempty_counts(codes, 3)} "
        f"tables={_nonempty_counts(tables, 3)} exceptions={_nonempty_counts(exceptions, 3)} "
        f"top_signals={[signal.summary for signal in signals[:2]]} "
        f"trace_ids={traces} sources={sources}"
    )


def sql_log_signals(records: Any) -> list[SqlLogSignal]:
    """Extract high-confidence SQL/TDDL root signals from SLS rows."""

    rows = _sls_rows(records)
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        content = _content(row)
        code = _first(TDDL_CODE_RE.findall(content)).upper()
        duplicate_value, duplicate_key = _duplicate_parts(content)
        table = _sql_table(content)
        if not (code or duplicate_key or table or "ERR_EXECUTE_ON_MYSQL" in content):
            continue
        key = (code or "SQL_ERROR", table or "unknown_table", duplicate_key or "")
        buckets.setdefault(key, []).append(row)

    signals: list[SqlLogSignal] = []
    for (code, table, duplicate_key), items in buckets.items():
        texts = [_content(item) for item in items]
        combined = "\n".join(texts[:3])
        duplicate_value, _ = _duplicate_parts(combined)
        group = _first(GROUP_RE.findall(combined))
        atom = _first(ATOM_RE.findall(combined))
        exceptions = _unique([exc for text in texts for exc in JAVA_EXCEPTION_RE.findall(text)], 5)
        trace_ids = _unique([trace for text in texts for trace in _trace_ids(text)], 5)
        sources = _unique(
            [str(item.get("__source__") or item.get("source") or "") for item in items], 5
        )
        score = 4.0
        if code.startswith("TDDL-"):
            score += 0.3
        if duplicate_key:
            score += 0.3
        if table and table != "unknown_table":
            score += 0.2
        if len(items) >= 5:
            score += 0.3
        label_parts = [code, table]
        if duplicate_key:
            label_parts.append(duplicate_key)
        label = ":".join(part for part in label_parts if part)
        summary = (
            f"code={code} table={table} count={len(items)} duplicate_key={duplicate_key} "
            f"duplicate_value={duplicate_value[:120]} group={group} atom={atom} "
            f"exceptions={exceptions} trace_ids={trace_ids} sources={sources}"
        )
        signals.append(
            SqlLogSignal(
                label=label,
                score=round(min(score, 5.0), 3),
                reason="business SLS SQL/TDDL error near alarm window",
                summary=summary,
                trace_ids=trace_ids,
                props={
                    "error_code": code,
                    "sql_table": table,
                    "duplicate_key": duplicate_key,
                    "duplicate_value": duplicate_value[:160],
                    "db_group": group,
                    "atom": atom,
                    "exceptions": exceptions,
                    "sources": sources,
                    "count": len(items),
                },
            )
        )
    signals.sort(key=lambda item: (-item.score, item.label))
    return signals


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


def _trace_ids(text: str) -> list[str]:
    return _unique(EAGLEEYE_RE.findall(text) + TRACE_RE.findall(text), 5)


def _tag_values(alarm: dict[str, Any], tag_name: str) -> list[str]:
    output: list[str] = []
    for group in alarm.get("alarm_tags") or []:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "") == tag_name:
                output.append(str(item.get("value") or ""))
    return output


def _first(values: list[str]) -> str:
    return values[0] if values else ""


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
