"""Datadog-backed upstream-evidence provider factory.

Construction of a Datadog provider lives here, not in
``app.pipeline.pipeline``, so the pipeline layer doesn't import from
``app.services.datadog``. Adding a new correlation source (Grafana,
AWS, …) follows the same shape: a sibling ``<vendor>_factory`` module
plus a registration in :mod:`app.agent.correlation.__init__`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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


def _datadog_avg_query(metric_name: str) -> str:
    metric = metric_name.strip()
    if metric.startswith(("avg:", "sum:", "min:", "max:", "count:")):
        return metric
    if "{" in metric and "}" in metric:
        return f"avg:{metric}"
    return f"avg:{metric}{{*}}"


def target_resource_from_state(state: dict[str, Any]) -> str:
    """Extract the Datadog ``target_resource`` (RDS DB identifier) from an
    investigation state's raw alert. Defaults to ``"unknown-rds"``.

    Public because the same key set is exercised by tests outside this
    module.
    """
    raw_alert = state.get("raw_alert") or {}
    if not isinstance(raw_alert, dict):
        return "unknown-rds"
    return str(
        raw_alert.get("resource")
        or raw_alert.get("resource_name")
        or raw_alert.get("db_instance")
        or raw_alert.get("db_instance_identifier")
        or "unknown-rds"
    )


def candidate_services_from_state(state: dict[str, Any]) -> tuple[str, ...]:
    """Extract upstream-service candidate names from a raw alert.

    Accepts a comma-separated string or a list/tuple under one of
    ``upstream_services`` / ``candidate_services`` / ``related_services``.
    Empty tuple when nothing relevant is present.
    """
    raw_alert = state.get("raw_alert") or {}
    if not isinstance(raw_alert, dict):
        return ()

    raw_candidates = (
        raw_alert.get("upstream_services")
        or raw_alert.get("candidate_services")
        or raw_alert.get("related_services")
    )
    if isinstance(raw_candidates, str):
        return tuple(item.strip() for item in raw_candidates.split(",") if item.strip())
    if isinstance(raw_candidates, list | tuple):
        return tuple(str(item).strip() for item in raw_candidates if str(item).strip())
    return ()


def build_datadog_provider(state: dict[str, Any]) -> UpstreamEvidenceProvider | None:
    """Return a Datadog-backed upstream-evidence provider, or ``None``.

    Returns ``None`` when the agent state has no resolved Datadog
    integration config (or a malformed one). Callers can treat that as
    "no Datadog correlation available for this run" — the top-level
    factory in :mod:`app.agent.correlation` is responsible for picking
    a different vendor or short-circuiting.
    """
    from app.integrations.config_models import DatadogIntegrationConfig
    from app.services.datadog import DatadogClient

    resolved = state.get("resolved_integrations") or {}
    datadog_cfg_raw = resolved.get("datadog")
    if not isinstance(datadog_cfg_raw, dict) or not datadog_cfg_raw:
        return None

    try:
        datadog_cfg = DatadogIntegrationConfig.model_validate(datadog_cfg_raw)
    except Exception:
        return None

    client = DatadogClient(datadog_cfg)

    def metric_query(metric_name: str, window: dict[str, Any]) -> dict[str, Any]:
        start = str(window.get("from") or "")
        end = str(window.get("to") or "")
        if not start or not end:
            return {"timestamps": [], "values": []}
        query = _datadog_avg_query(metric_name)
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
            upstream_service_names=candidate_services_from_state(state),
        ),
        target_resource=target_resource_from_state(state),
    )
