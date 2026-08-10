"""JSON:API primitives shared by client.py and on-call shapers.

Rootly speaks JSON:API, not plain JSON. Every resource arrives as
``{"data": {"id", "type", "attributes"}}`` rather than a flat object. These
helpers unwrap that envelope consistently.

Breaking out these primitives prevents a circular import: on_call.py needs them
and client.py needs them, but importing from client.py into on_call.py while
client.py imports the on-call shapers back trips CodeQL py/cyclic-import.
"""

from __future__ import annotations

from typing import Any


def attributes(record: Any) -> dict[str, Any]:
    """Pull ``attributes`` out of one JSON:API resource object."""
    if not isinstance(record, dict):
        return {}
    attributes_data = record.get("attributes")
    return attributes_data if isinstance(attributes_data, dict) else {}


def data_list(payload: Any) -> list[dict[str, Any]]:
    """Extract the ``data`` array from a JSON:API list response."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def meta_total(payload: Any) -> int | None:
    """Extract ``meta.total_count`` from a JSON:API response, or None."""
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    total = meta.get("total_count")
    return total if isinstance(total, int) else None


def truncate(text: str, limit: int) -> str:
    """Truncate text to a maximum length with ellipsis."""
    return text if len(text) <= limit else f"{text[:limit]}…"


def named(value: Any) -> str:
    """Extract a name from Rootly's nested structures.

    Rootly nests severity/status names and other identifiers; accept either a
    string or an object with name/slug/title keys.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "slug", "title"):
            found = value.get(key)
            if isinstance(found, str) and found:
                return found
    return ""


def clamp(value: int | None, default: int, maximum: int) -> int:
    """Clamp a page size to valid bounds."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


__all__ = [
    "attributes",
    "data_list",
    "meta_total",
    "truncate",
    "named",
    "clamp",
]
