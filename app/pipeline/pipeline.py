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


def _build_correlation_config(state: dict[str, Any]) -> dict[str, Any] | None:
    from app.correlation.datadog_adapter import DatadogCorrelationAdapter
    from app.correlation.datadog_provider import DatadogUpstreamEvidenceProvider
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
        query = f"avg:{metric_name}{{*}}"
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
        minutes = _window_minutes(start, end)
        result = client.search_logs(query, time_range_minutes=minutes, limit=100)
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
        )
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
    from app.delivery import deliver
    from app.nodes.investigate.correlate_upstream import node_correlate_upstream
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
