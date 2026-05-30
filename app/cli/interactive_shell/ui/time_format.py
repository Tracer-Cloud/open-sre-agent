"""Shared timestamp and duration formatting for REPL tables and menus."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

ReplTimestampStyle = Literal["table", "compact", "utc"]


def format_repl_duration(duration_secs: int | None) -> str:
    """Format a duration in seconds for REPL session/task tables."""
    if duration_secs is None:
        return "—"
    if duration_secs < 60:
        return f"{duration_secs}s"
    if duration_secs < 3600:
        return f"{duration_secs // 60}m {duration_secs % 60}s"
    hours = duration_secs // 3600
    minutes = (duration_secs % 3600) // 60
    return f"{hours}h {minutes}m"


def format_repl_timestamp(
    value: str | datetime | int | float | None,
    *,
    style: ReplTimestampStyle = "table",
    fallback: str = "—",
) -> str:
    """Format ISO strings, datetimes, or unix timestamps for REPL display."""
    if value is None:
        return fallback
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=UTC)
    else:
        raw = value.strip()
        if not raw:
            return fallback
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return raw[:16] if raw else fallback

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    if style == "utc":
        return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    local = dt.astimezone()
    if style == "compact":
        return local.strftime("%m-%d %H:%M")
    return local.strftime("%Y-%m-%d %H:%M")
