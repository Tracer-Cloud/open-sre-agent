"""Datadog-backed upstream-evidence provider factory.

Construction of a Datadog provider lives here, not in
``app.pipeline.pipeline``, so the pipeline layer doesn't import from
``app.services.datadog``. Adding a new correlation source (Grafana,
AWS, …) follows the same shape: a sibling ``<vendor>_factory`` module
plus a registration in :mod:`app.agent.correlation.__init__`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.agent.correlation.datadog_adapter import DatadogCorrelationAdapter
from app.agent.correlation.datadog_provider import (
    DatadogCorrelationQueries,
    DatadogUpstreamEvidenceProvider,
)

if TYPE_CHECKING:
    from app.agent.correlation.upstream import UpstreamEvidenceProvider


def _parse_iso8601(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _window_minutes(start: str, end: str) -> int:
    try:
        delta = _parse_iso8601(end) - _parse_iso8601(start)
        return max(1, int(delta.total_seconds() // 60))
    except Exception:
        return 60


def datadog_avg_query(metric_name: str) -> str:
    metric = metric_name.strip()
    if metric.startswith(("avg:", "sum:", "min:", "max:", "count:")):
        return metric
    if "{" in metric and "}" in metric:
        return f"avg:{metric}"
    return f"avg:{metric}{{*}}"


def build_datadog_provider(
    *,
    datadog_config: Mapping[str, Any] | None,
    target_resource: str = "unknown-rds",
    candidate_services: tuple[str, ...] = (),
) -> UpstreamEvidenceProvider | None:
    """Return a Datadog-backed upstream-evidence provider, or ``None``.

    Callers pass the integration config and the alert-derived knobs
    directly; the factory does **not** know about agent state shape.
    State extraction lives in :mod:`app.agent.correlation.__init__`.

    Returns ``None`` when ``datadog_config`` is empty/missing or fails
    Pydantic validation — both signal "no Datadog correlation available
    for this run".
    """
    from app.integrations.config_models import DatadogIntegrationConfig
    from app.services.datadog import DatadogClient

    if not datadog_config:
        return None

    try:
        datadog_cfg = DatadogIntegrationConfig.model_validate(datadog_config)
    except ValidationError:
        return None

    client = DatadogClient(datadog_cfg)

    def metric_query(metric_name: str, window: dict[str, Any]) -> dict[str, Any]:
        start = str(window.get("from") or "")
        end = str(window.get("to") or "")
        if not start or not end:
            return {"timestamps": [], "values": []}
        query = datadog_avg_query(metric_name)
        result = client.query_metrics(query, start=_parse_iso8601(start), end=_parse_iso8601(end))
        if not result.get("success"):
            return {"timestamps": [], "values": []}
        return {
            "timestamps": result.get("timestamps") or [],
            "values": result.get("values") or [],
        }

    def log_query(query: str, window: dict[str, Any]) -> dict[str, Any]:
        start = str(window.get("from") or "")
        end = str(window.get("to") or "")
        start_dt = _parse_iso8601(start) if start else None
        end_dt = _parse_iso8601(end) if end else None
        minutes = _window_minutes(start, end)
        result = client.search_logs(
            query,
            time_range_minutes=minutes,
            limit=100,
            start=start_dt,
            end=end_dt,
        )
        logs = result.get("logs") if isinstance(result, dict) else []
        if not isinstance(logs, list):
            logs = []
        return {
            "timestamps": [
                str(item.get("timestamp", "")) for item in logs if isinstance(item, dict)
            ],
            "messages": [str(item.get("message", "")) for item in logs if isinstance(item, dict)],
        }

    return DatadogUpstreamEvidenceProvider(
        adapter=DatadogCorrelationAdapter(
            metric_query_fn=metric_query,
            log_query_fn=log_query,
        ),
        queries=DatadogCorrelationQueries(
            upstream_service_names=candidate_services,
        ),
        target_resource=target_resource,
    )
