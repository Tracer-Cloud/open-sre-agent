"""Scheduling helpers for work-item reminders."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_work_item_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC)


def cron_from_datetime(value: datetime) -> str:
    return f"{value.minute} {value.hour} {value.day} {value.month} *"


__all__ = ["cron_from_datetime", "parse_work_item_datetime"]
