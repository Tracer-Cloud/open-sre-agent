"""Argument validation helpers for work-item tools."""

from __future__ import annotations

from typing import Any

from core.domain.work_items import (
    WORK_ITEM_PRIORITIES,
    WORK_ITEM_STATUSES,
    WorkItemPriority,
    WorkItemStatus,
    parse_priority,
    parse_status,
)

DEFAULT_WORK_ITEM_LIMIT = 20
MAX_WORK_ITEM_LIMIT = 100
DEFAULT_PRIORITY_LIMIT = 5


def normalize_limit(value: Any, *, default: int = DEFAULT_WORK_ITEM_LIMIT) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(int(value), 0), MAX_WORK_ITEM_LIMIT)


def normalize_priority(value: Any) -> WorkItemPriority | None:
    try:
        return parse_priority(value or WorkItemPriority.NORMAL.value)
    except ValueError:
        return None


def normalize_status_filter(value: Any) -> WorkItemStatus | None | str:
    text = str(value or "open").strip().lower()
    if text in {"", "all", "any"}:
        return None
    if text == "active":
        return "active"
    try:
        return parse_status(text)
    except ValueError:
        return "invalid"


def normalize_selectors(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.replace(",", " ").split() if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def valid_priority_detail() -> str:
    return f"priority must be one of {', '.join(WORK_ITEM_PRIORITIES)}"


def valid_status_detail() -> str:
    return f"status must be one of {', '.join(WORK_ITEM_STATUSES)}"


__all__ = [
    "DEFAULT_PRIORITY_LIMIT",
    "DEFAULT_WORK_ITEM_LIMIT",
    "MAX_WORK_ITEM_LIMIT",
    "normalize_limit",
    "normalize_priority",
    "normalize_selectors",
    "normalize_status_filter",
    "valid_priority_detail",
    "valid_status_detail",
]
