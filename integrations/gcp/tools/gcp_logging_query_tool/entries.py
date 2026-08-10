"""Cloud Logging entry normalization.

A ``LogEntry`` carries its payload in exactly one of three mutually exclusive
fields — ``textPayload``, ``jsonPayload``, or ``protoPayload`` — and which one
appears depends on the producer, not on the caller. GKE container stdout is
``textPayload``; structured application logs and Cloud Audit Logs are
``jsonPayload``/``protoPayload``. Reading only ``textPayload`` (the obvious
implementation) silently returns empty messages for exactly the audit and
structured entries an investigation most needs.
"""

from __future__ import annotations

import json
from typing import Any

#: Per-message cap. Long enough for a stack trace, short enough that a full
#: page of entries does not blow the context budget.
_MAX_MESSAGE_CHARS = 2000

#: Keys that structured payloads conventionally use for the human-readable line.
_MESSAGE_KEYS = ("message", "msg", "textPayload", "log", "event", "description")


def _truncate(text: str) -> str:
    if len(text) <= _MAX_MESSAGE_CHARS:
        return text
    return f"{text[:_MAX_MESSAGE_CHARS]}… [truncated {len(text) - _MAX_MESSAGE_CHARS} chars]"


def extract_message(entry: dict[str, Any]) -> str:
    """Return the most useful human-readable line from any payload shape."""
    text = entry.get("textPayload")
    if isinstance(text, str) and text.strip():
        return _truncate(text.strip())

    for key in ("jsonPayload", "protoPayload"):
        payload = entry.get(key)
        if not isinstance(payload, dict):
            continue
        for candidate in _MESSAGE_KEYS:
            value = payload.get(candidate)
            if isinstance(value, str) and value.strip():
                return _truncate(value.strip())
        # No conventional message key: serialize the whole payload rather than
        # returning nothing. Audit entries put the useful content in
        # methodName/resourceName/status, which no single key covers.
        try:
            return _truncate(json.dumps(payload, sort_keys=True, default=str))
        except (TypeError, ValueError):
            return _truncate(str(payload))

    return ""


def normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten one ``LogEntry`` into the compact shape the agent consumes."""
    resource = entry.get("resource") or {}
    labels = resource.get("labels") or {} if isinstance(resource, dict) else {}
    normalized: dict[str, Any] = {
        "timestamp": entry.get("timestamp", ""),
        "severity": entry.get("severity", "DEFAULT"),
        "message": extract_message(entry),
        "log_name": str(entry.get("logName", "")).rsplit("/", 1)[-1],
        "resource_type": resource.get("type", "") if isinstance(resource, dict) else "",
    }
    project = labels.get("project_id") if isinstance(labels, dict) else ""
    if project:
        normalized["project_id"] = project
    for label in ("cluster_name", "namespace_name", "pod_name", "container_name"):
        value = labels.get(label) if isinstance(labels, dict) else ""
        if value:
            normalized[label] = value
    trace = entry.get("trace")
    if isinstance(trace, str) and trace:
        # projects/<id>/traces/<hex> — the hex alone is what correlates.
        normalized["trace"] = trace.rsplit("/", 1)[-1]
    return normalized


def normalize_entries(entries: list[Any]) -> list[dict[str, Any]]:
    """Normalize a page of entries, skipping anything that is not an object."""
    return [normalize_entry(entry) for entry in entries if isinstance(entry, dict)]
