"""Normalize investigation state into report-context inputs."""

from __future__ import annotations

import time
from typing import Any

from app.state import InvestigationState


def safe_get(data: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    """Safely navigate nested dictionaries without raising."""
    if data is None:
        return default
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def as_snippet(value: str | None, max_len: int = 140) -> str | None:
    """Compact a value to a short, brace-free snippet for display."""
    if not value:
        return None
    compact = " ".join(str(value).split())
    compact = compact.replace("{", "").replace("}", "").replace("[", "").replace("]", "")
    return compact[:max_len]


def filter_valid_claims(claims: list[dict]) -> list[dict]:
    """Drop claims with empty text or the NON_ artifact prefix."""
    return [
        c
        for c in claims
        if c.get("claim", "").strip() and not c.get("claim", "").strip().startswith("NON_")
    ]


class NormalizedState:
    """All raw data extracted from InvestigationState in one place."""

    def __init__(self, state: InvestigationState) -> None:
        evidence = state.get("evidence", {}) or {}
        available_sources = state.get("available_sources", {}) or {}
        raw_alert_value = state.get("raw_alert", {})

        self.evidence: dict[str, Any] = evidence
        self.raw_alert: dict[str, Any] = (
            raw_alert_value if isinstance(raw_alert_value, dict) else {}
        )
        self.s3: dict[str, Any] = evidence.get("s3", {}) or {}
        self.available_sources: dict[str, dict[str, Any]] = available_sources

        self.grafana_endpoint: str | None = (available_sources.get("grafana") or {}).get(
            "grafana_endpoint"
        )
        self.datadog_site: str = (available_sources.get("datadog") or {}).get(
            "site"
        ) or "datadoghq.com"

        self.validated_claims: list[dict] = filter_valid_claims(state.get("validated_claims", []))
        self.non_validated_claims: list[dict] = state.get("non_validated_claims", [])

        (
            self.cloudwatch_url,
            self.cloudwatch_group,
            self.cloudwatch_stream,
            self.cloudwatch_region,
            self.alert_id,
        ) = extract_cloudwatch_info(self.raw_alert)

        started_at = state.get("investigation_started_at")
        self.duration_seconds: int | None = (
            max(0, int(round(time.monotonic() - float(started_at))))
            if isinstance(started_at, int | float)
            else None
        )

        self.state = state


def extract_cloudwatch_info(
    raw_alert: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Pull CloudWatch metadata from an alert dict.

    Returns: (url, log_group, log_stream, region, alert_id)
    """
    annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
    if not annotations and raw_alert.get("alerts"):
        first_alert = raw_alert.get("alerts", [{}])[0]
        if isinstance(first_alert, dict):
            annotations = first_alert.get("annotations", {}) or {}

    url = (
        raw_alert.get("cloudwatch_logs_url")
        or raw_alert.get("cloudwatch_url")
        or safe_get(annotations, "cloudwatch_logs_url")
        or safe_get(annotations, "cloudwatch_url")
    )
    group = raw_alert.get("cloudwatch_log_group") or safe_get(annotations, "cloudwatch_log_group")
    stream = raw_alert.get("cloudwatch_log_stream") or safe_get(
        annotations, "cloudwatch_log_stream"
    )
    region = raw_alert.get("cloudwatch_region") or safe_get(annotations, "cloudwatch_region")
    alert_id = raw_alert.get("alert_id")
    return url, group, stream, region, alert_id
