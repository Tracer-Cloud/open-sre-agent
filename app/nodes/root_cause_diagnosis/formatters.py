"""Log entry formatters for root cause diagnosis prompt construction."""

from __future__ import annotations

import datetime
from typing import Any

from app.nodes.root_cause_diagnosis.constants import GRAFANA_SOURCE_TYPE_LABELS

_STRUCTURED_TAG_PREFIXES = ("kube_",)
_STRUCTURED_TAG_NAMES = ("pod_name", "container_name", "container_id")


def _format_grafana_log_entry(log: Any) -> str:
    if not isinstance(log, dict):
        return str(log)[:300]

    message = str(log.get("message") or "")[:300]
    source_type = str(log.get("source_type") or "").strip()
    source_parts = [
        GRAFANA_SOURCE_TYPE_LABELS.get(source_type, ""),
        str(log.get("source_identifier") or "").strip(),
    ]
    source = " ".join(part for part in source_parts if part)
    if not source:
        return message
    return f"[{source}] {message}" if message else f"[{source}]"


def _db_load_value(item: dict[str, Any]) -> Any:
    db_load = item.get("db_load")
    return item.get("db_load_avg") if db_load is None else db_load


def _format_wait_events(wait_events: list[Any]) -> str:
    formatted: list[str] = []
    for wait in wait_events[:3]:
        if not isinstance(wait, dict):
            continue
        name = wait.get("name") or wait.get("wait_event") or "unknown"
        db_load = _db_load_value(wait)
        if db_load is None:
            formatted.append(str(name))
        else:
            formatted.append(f"{name}({db_load})")
    return ", ".join(formatted)


def _format_datadog_log_entry(log: Any) -> str:
    """Format a single Datadog log entry, surfacing structured tags and timestamp when present."""
    if not isinstance(log, dict):
        return str(log)[:300]

    message = log.get("message", "")[:300]
    tags = log.get("tags", [])

    # Extract HH:MM:SS from ISO timestamp for compact display
    ts_prefix = ""
    raw_ts = log.get("timestamp", "")
    if isinstance(raw_ts, str) and "T" in raw_ts:
        time_part = raw_ts.split("T", 1)[1][:8]  # "HH:MM:SS"
        ts_prefix = f"[{time_part}] "
    elif isinstance(raw_ts, int | float):
        ts_prefix = f"[{datetime.datetime.utcfromtimestamp(raw_ts / 1000 if raw_ts > 1e10 else raw_ts).strftime('%H:%M:%S')}] "

    tag_parts: dict[str, str] = {}
    for t in tags:
        if not isinstance(t, str) or ":" not in t:
            continue
        k, _, v = t.partition(":")
        if any(k.startswith(p) for p in _STRUCTURED_TAG_PREFIXES) or k in _STRUCTURED_TAG_NAMES:
            tag_parts[k] = v

    if tag_parts:
        tag_str = " ".join(f"{k}={v}" for k, v in tag_parts.items())
        return f"{ts_prefix}[{tag_str}] {message}"

    host = log.get("host", "")
    service = log.get("service", "")
    prefix = f"[{service}@{host}] " if service or host else ""
    return f"{ts_prefix}{prefix}{message}"


def _extract_vercel_git_metadata(meta: dict[str, Any]) -> dict[str, str]:
    """Normalize git metadata from Vercel deployment evidence."""
    return {
        "repo": str(meta.get("github_repo") or meta.get("githubRepo") or "").strip(),
        "sha": str(meta.get("github_commit_sha") or meta.get("githubCommitSha") or "").strip(),
        "ref": str(meta.get("github_commit_ref") or meta.get("githubCommitRef") or "").strip(),
    }


def _format_vercel_runtime_log(log: Any) -> str:
    """Format a runtime log entry into a compact single-line excerpt."""
    if not isinstance(log, dict):
        return str(log)[:300]

    message = log.get("message")
    if not message:
        payload = log.get("payload")
        if isinstance(payload, dict):
            message = payload.get("text") or payload.get("message") or payload.get("body") or ""
        elif payload:
            message = str(payload)

    prefix_parts = [
        str(log.get("type", "")).strip(),
        str(log.get("source", "")).strip(),
    ]
    prefix = " ".join(part for part in prefix_parts if part)
    text = str(message or "")[:260]
    return f"{prefix}: {text}" if prefix else text
