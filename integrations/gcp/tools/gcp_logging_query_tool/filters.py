"""Cloud Logging filter construction.

Kept separate from the tool entrypoint so filter assembly can be tested without
building an API client.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

#: Cloud Logging severity ladder, ascending. ``severity >= X`` in a filter
#: matches everything at or above X on this ladder.
SEVERITIES: tuple[str, ...] = (
    "DEFAULT",
    "DEBUG",
    "INFO",
    "NOTICE",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "ALERT",
    "EMERGENCY",
)

_MAX_HOURS = 24 * 30


def normalize_severity(severity: str) -> str | None:
    """Return the canonical severity name, or ``None`` when unset/unknown.

    Unknown values are dropped rather than rejected: a bad severity should
    widen the query, never fail the call, because the log content is still
    what the investigation needs.
    """
    candidate = (severity or "").strip().upper()
    return candidate if candidate in SEVERITIES else None


def clamp_hours(hours: float) -> float:
    """Bound the lookback window to something Cloud Logging will answer for."""
    try:
        value = float(hours)
    except (TypeError, ValueError):
        return 1.0
    if value <= 0:
        return 1.0
    return min(value, _MAX_HOURS)


def build_filter(
    *,
    user_filter: str = "",
    severity: str = "",
    hours: float = 1.0,
    now: datetime | None = None,
) -> str:
    """Compose a Cloud Logging filter from the caller's clauses plus a window.

    The time bound is always applied. Without it Cloud Logging scans the full
    retention period, which on a busy project is slow enough to time out the
    tool call and returns entries too old to be relevant to a live incident.
    """
    moment = now or datetime.now(UTC)
    start = moment - timedelta(hours=clamp_hours(hours))
    clauses = [f'timestamp >= "{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"']

    canonical = normalize_severity(severity)
    if canonical:
        clauses.append(f"severity >= {canonical}")

    extra = (user_filter or "").strip()
    if extra:
        # Parenthesised so an OR inside the caller's filter cannot escape and
        # disable the time bound above.
        clauses.append(f"({extra})")

    return " AND ".join(clauses)
