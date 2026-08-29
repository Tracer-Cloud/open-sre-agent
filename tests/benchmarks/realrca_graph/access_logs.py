from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class AccessLogSignal:
    """Compact root-cause signal extracted from HTTP access logs."""

    label: str
    score: float
    reason: str
    summary: str
    trace_ids: list[str]
    props: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def should_query_access_logs(case_type: str, alarm: dict[str, Any]) -> bool:
    """Return whether access logs are likely useful for this alarm."""

    text = " ".join(
        str(alarm.get(key) or "") for key in ("metric", "monitor_item_name", "title", "content")
    ).lower()
    if case_type == "自定义监控":
        return True
    return any(
        marker in text
        for marker in (
            "nginx",
            "http",
            "web",
            "uri",
            "4xx",
            "5xx",
            "400",
            "失败数",
            "后端代理",
        )
    )


def access_log_search_terms(alarm: dict[str, Any], *, limit: int = 4) -> list[str]:
    """Build SLS search terms from visible alarm tags and text."""

    terms: list[str] = []
    for group in alarm.get("alarm_tags") or []:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            if _safe_search_term(value):
                terms.append(value)
    for value in _candidate_words(str(alarm.get("content") or "")):
        if _safe_search_term(value):
            terms.append(value)
    return _unique(terms, limit)


def summarize_access_logs(records: Any) -> str:
    """Summarize SLS access log rows without retaining long request URIs."""

    rows = _access_rows(records)
    if not rows:
        return "access_logs count=0 top="
    status_counts = Counter(str(row.get("status") or "") for row in rows)
    paths = Counter(_request_path(row) for row in rows)
    methods = Counter(str(row.get("request_method") or "") for row in rows)
    max_uri = max((_request_uri_length(row) for row in rows), default=0)
    max_param_count = max((_max_repeated_param_count(row) for row in rows), default=0)
    trace_ids = _unique(
        [str(row.get("eagleeye_traceid") or row.get("trace_id") or "") for row in rows],
        4,
    )
    sources = _unique(
        [str(row.get("__source__") or row.get("source") or row.get("host") or "") for row in rows],
        4,
    )
    return (
        f"access_logs count={len(rows)} statuses={dict(status_counts)} "
        f"methods={dict(methods)} top_paths={dict(paths.most_common(3))} "
        f"max_uri_len={max_uri} max_repeated_param_count={max_param_count} "
        f"trace_ids={trace_ids} sources={sources}"
    )


def access_log_signals(records: Any, *, min_error_status: int = 400) -> list[AccessLogSignal]:
    """Extract high-confidence HTTP access-log root signals."""

    rows = _access_rows(records)
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        status = _status_code(row)
        if status < min_error_status:
            continue
        key = (str(status), _request_path(row) or "unknown")
        buckets.setdefault(key, []).append(row)

    signals: list[AccessLogSignal] = []
    for (status, path), items in buckets.items():
        max_uri = max((_request_uri_length(item) for item in items), default=0)
        max_param_count = max((_max_repeated_param_count(item) for item in items), default=0)
        trace_ids = _unique(
            [str(item.get("eagleeye_traceid") or item.get("trace_id") or "") for item in items],
            4,
        )
        request_times = [_request_time_ms(item) for item in items]
        request_times = [value for value in request_times if value is not None]
        score = 3.5
        if len(items) >= 3:
            score += 0.4
        if max_uri >= 2048:
            score += 0.7
        if max_param_count >= 100:
            score += 0.5
        if status.startswith("5"):
            score += 0.2
        if status == "401":
            score += 0.35
        summary = (
            f"http_status={status} path={path} count={len(items)} "
            f"max_uri_len={max_uri} max_repeated_param_count={max_param_count} "
            f"trace_ids={trace_ids}"
        )
        if request_times:
            summary += (
                f" min_request_ms={min(request_times):.3f} max_request_ms={max(request_times):.3f}"
            )
        signals.append(
            AccessLogSignal(
                label=f"http_{status}:{path}",
                score=round(min(score, 5.0), 3),
                reason=(
                    "HTTP 401 access log authentication failure near alarm window"
                    if status == "401"
                    else "HTTP access log error near alarm window"
                ),
                summary=summary,
                trace_ids=trace_ids,
                props={
                    "status": status,
                    "path": path,
                    "count": len(items),
                    "max_uri_len": max_uri,
                    "max_repeated_param_count": max_param_count,
                    "auth_failure": status == "401",
                },
            )
        )
    signals.sort(key=lambda item: (-item.score, item.label))
    return signals


def _access_rows(records: Any) -> list[dict[str, Any]]:
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


def _request_path(row: dict[str, Any]) -> str:
    uri = str(row.get("request_uri") or row.get("uri") or row.get("path") or "")
    if not uri:
        return ""
    parsed = urlsplit(uri)
    return parsed.path or uri.split("?", 1)[0]


def _request_uri_length(row: dict[str, Any]) -> int:
    return len(str(row.get("request_uri") or row.get("uri") or ""))


def _max_repeated_param_count(row: dict[str, Any]) -> int:
    uri = str(row.get("request_uri") or row.get("uri") or "")
    query = urlsplit(uri).query
    if not query:
        return 0
    counts = []
    for values in parse_qs(query, keep_blank_values=True).values():
        counts.extend(_comma_count(value) for value in values)
    return max(counts, default=0)


def _comma_count(value: str) -> int:
    if not value:
        return 0
    return value.count(",") + 1


def _status_code(row: dict[str, Any]) -> int:
    try:
        return int(str(row.get("status") or row.get("status_code") or "0"))
    except ValueError:
        return 0


def _request_time_ms(row: dict[str, Any]) -> float | None:
    raw = row.get("request_time_usec")
    if raw is not None:
        try:
            return float(raw) / 1000.0
        except (TypeError, ValueError):
            return None
    raw = row.get("request_time")
    if raw is None:
        return None
    try:
        return float(raw) * 1000.0
    except (TypeError, ValueError):
        return None


def _candidate_words(text: str) -> list[str]:
    output: list[str] = []
    for marker in ("goc", "block", "nginx", "http", "api"):
        if marker not in text.lower():
            continue
        for raw in text.replace("/", " ").replace("?", " ").split():
            value = raw.strip("[](){}<>,.;:'\"")
            if 3 <= len(value) <= 80 and any(char.isalpha() for char in value):
                output.append(value)
    return output


def _safe_search_term(value: str) -> bool:
    return (
        2 <= len(value) <= 80
        and "\n" not in value
        and "\r" not in value
        and not value.startswith(("http://", "https://"))
    )


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
