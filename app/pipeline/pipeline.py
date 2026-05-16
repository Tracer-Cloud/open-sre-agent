"""Raw-alert-first connected investigation coordinator."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

from app.state import AgentState

logger = logging.getLogger(__name__)


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


def _target_resource_from_state(state: dict[str, Any]) -> str:
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


def _candidate_services_from_state(state: dict[str, Any]) -> tuple[str, ...]:
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


def _build_correlation_config(state: dict[str, Any]) -> dict[str, Any] | None:
    from app.correlation.datadog_adapter import DatadogCorrelationAdapter
    from app.correlation.datadog_provider import (
        DatadogCorrelationQueries,
        DatadogUpstreamEvidenceProvider,
    )
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

    provider = DatadogUpstreamEvidenceProvider(
        adapter=DatadogCorrelationAdapter(
            metric_query_fn=metric_query,
            log_query_fn=log_query,
        ),
        queries=DatadogCorrelationQueries(
            upstream_service_names=_candidate_services_from_state(state),
        ),
        target_resource=_target_resource_from_state(state),
    )
    return {"configurable": {"upstream_evidence_provider": provider}}


def run_connected_investigation(state: AgentState) -> AgentState:
    """Resolve connected integrations → parse alert → agent loop → deliver.

    All steps mutate a shared state dict. Each step returns a dict of updates
    which are merged in. Pure function: inputs in, state out.
    """
    from app.agent.context import resolve_integrations
    from app.agent.extract import extract_alert
    from app.agent.investigation import ConnectedInvestigationAgent
    from app.correlation.node import node_correlate_upstream
    from app.delivery import deliver
    from app.utils.sentry_sdk import capture_exception

    state_any = cast(dict[str, Any], state)

    try:
        _merge(state_any, {"resolved_integrations": resolve_integrations(state)})

        _merge(state_any, extract_alert(state))
        if state_any.get("is_noise"):
            return cast(AgentState, state_any)

        _merge(state_any, ConnectedInvestigationAgent().run(state_any))
        _merge(
            state_any,
            node_correlate_upstream(
                cast(AgentState, state_any),
                _build_correlation_config(state_any),
            ),
        )

        _merge(state_any, deliver(state))
    except Exception as exc:
        capture_exception(exc)
        raise

    return cast(AgentState, state_any)


def run_investigation(state: AgentState) -> AgentState:
    """Backward-compatible alias for the connected investigation coordinator."""
    return run_connected_investigation(state)


def run_chat(state: AgentState) -> AgentState:
    """Run a single chat turn via ChatAgent."""
    from app.agent.chat import ChatAgent
    from app.utils.sentry_sdk import capture_exception

    state_any = cast(dict[str, Any], state)
    try:
        updates = ChatAgent().run(state)
        _merge(state_any, updates)
    except Exception as exc:
        capture_exception(exc)
        raise
    return cast(AgentState, state_any)


def _merge(state: dict[str, Any], updates: dict[str, Any]) -> None:
    if not updates:
        return
    for key, value in updates.items():
        if key == "messages":
            messages = list(state.get("messages") or [])
            if isinstance(value, list):
                messages.extend(value)
            else:
                messages.append(value)
            state["messages"] = messages
        else:
            state[key] = value
