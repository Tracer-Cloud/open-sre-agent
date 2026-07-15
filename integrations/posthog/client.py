"""PostHog HTTP transport and analytics queries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from config.constants.posthog import DEFAULT_POSTHOG_BOUNCE_WINDOW
from integrations.posthog.config import PostHogConfig


@dataclass(frozen=True)
class BounceRateResult:
    bounce_rate: float
    total_sessions: int
    bounced_sessions: int
    period: str
    queried_at: datetime


@dataclass(frozen=True)
class BounceRateAlert:
    bounce_rate: float
    threshold: float
    total_sessions: int
    bounced_sessions: int
    period: str
    severity: str
    message: str


_HOGQL_INTERVAL_RE = re.compile(
    r"^(?P<count>\d+)\s*(?P<unit>h|hour|hours|d|day|days|w|week|weeks)$",
    re.IGNORECASE,
)


def _hogql_interval(period: str) -> str:
    """Convert compact period (e.g. ``24h``) to HogQL ``INTERVAL`` form (``24 HOUR``)."""
    stripped = period.strip()
    if " " in stripped:
        normalized = stripped.upper()
        for plural, singular in (("HOURS", "HOUR"), ("DAYS", "DAY"), ("WEEKS", "WEEK")):
            normalized = normalized.replace(plural, singular)
        return normalized

    match = _HOGQL_INTERVAL_RE.match(stripped)
    if not match:
        raise ValueError(f"Invalid PostHog period: {period!r}")

    count = match.group("count")
    unit = match.group("unit").lower()
    if unit in {"h", "hour", "hours"}:
        return f"{count} HOUR"
    if unit in {"d", "day", "days"}:
        return f"{count} DAY"
    if unit in {"w", "week", "weeks"}:
        return f"{count} WEEK"
    raise ValueError(f"Invalid PostHog period: {period!r}")


def _request_json(
    config: PostHogConfig,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    url = f"{config.api_base_url}{path}"
    response = httpx.request(
        method,
        url,
        headers=config.auth_headers,
        params=params,
        json=json,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def query_bounce_rate(
    config: PostHogConfig,
    *,
    period: str = DEFAULT_POSTHOG_BOUNCE_WINDOW,
) -> BounceRateResult:
    hogql_period = _hogql_interval(period)
    payload = _request_json(
        config,
        "POST",
        f"/api/projects/{config.project_id}/query/",
        json={
            "query": {
                "kind": "HogQLQuery",
                "query": (
                    "SELECT "
                    "countIf($session_duration <= 10) AS bounced_sessions, "
                    "count() AS total_sessions "
                    "FROM sessions "
                    f"WHERE $start_timestamp >= now() - INTERVAL {hogql_period}"
                ),
            }
        },
    )

    if not isinstance(payload, dict):
        raise ValueError("Unexpected PostHog response")

    results = payload.get("results", [])
    if not results:
        raise ValueError("Empty PostHog response")

    row = results[0]

    bounced_sessions = int(row[0])
    total_sessions = int(row[1])

    bounce_rate = 0.0
    if total_sessions > 0:
        bounce_rate = min(bounced_sessions / total_sessions, 1.0)

    return BounceRateResult(
        bounce_rate=bounce_rate,
        total_sessions=total_sessions,
        bounced_sessions=bounced_sessions,
        period=period,
        queried_at=datetime.now(UTC),
    )


def check_bounce_rate_alert(config: PostHogConfig) -> BounceRateAlert | None:
    result = query_bounce_rate(config, period=config.bounce_rate_window)

    if result.bounce_rate <= config.bounce_rate_threshold:
        return None

    severity = "critical" if result.bounce_rate > 0.9 else "warning"

    bounce_pct = round(result.bounce_rate * 100, 1)
    threshold_pct = round(config.bounce_rate_threshold * 100, 1)

    return BounceRateAlert(
        bounce_rate=result.bounce_rate,
        threshold=config.bounce_rate_threshold,
        total_sessions=result.total_sessions,
        bounced_sessions=result.bounced_sessions,
        period=result.period,
        severity=severity,
        message=(
            f"Bounce rate is {bounce_pct}% over the last {result.period}, "
            f"above threshold {threshold_pct}%."
        ),
    )
