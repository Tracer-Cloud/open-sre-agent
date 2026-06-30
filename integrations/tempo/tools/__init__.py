# ======== from tools/tempo_tool/ ========

"""Grafana Tempo trace query tool (single action-based entrypoint)."""

from __future__ import annotations

from typing import Any

from integrations.tempo import TempoConfig, tempo_extract_params
from integrations.tempo.client import TempoClient
from tools.tool_decorator import tool
from tools.utils.availability import tempo_available_or_backend

_VALID_ACTIONS = ("search", "get_trace", "list_services", "list_span_names")


def _tempo_is_available(sources: dict[str, dict]) -> bool:
    return tempo_available_or_backend(sources)


def _tempo_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    tempo = sources.get("tempo", {})
    return {
        **tempo_extract_params(sources),
        "service": tempo.get("service_name", ""),
        "time_range_minutes": tempo.get("time_range_minutes", 60),
        "limit": 20,
        "tempo_backend": tempo.get("_backend"),
    }


def _dispatch(
    client: Any,
    *,
    action: str,
    trace_id: str | None,
    service: str | None,
    span_name: str | None,
    min_duration_ms: float | None,
    max_duration_ms: float | None,
    tags: dict[str, str] | None,
    time_range_minutes: int,
    limit: int,
) -> dict[str, Any]:
    result: dict[str, Any]
    if action == "get_trace":
        result = client.get_trace_by_id(trace_id or "")
    elif action == "list_services":
        result = client.list_services(time_range_minutes=time_range_minutes)
    elif action == "list_span_names":
        result = client.list_span_names(time_range_minutes=time_range_minutes)
    else:
        result = client.search_traces(
            service=service,
            span_name=span_name,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            tags=tags,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )
    return result


@tool(
    name="query_tempo",
    display_name="Grafana Tempo",
    source="tempo",
    tags=("traces", "observability"),
    cost_tier="moderate",
    description=(
        "Query a standalone Grafana Tempo backend for distributed traces. "
        "Use 'action' to pick: search traces, fetch a trace by ID, or list "
        "registered services / span names."
    ),
    use_cases=[
        "Fetching a full trace by trace ID to inspect its spans",
        "Searching traces by service, span name, duration, or tags",
        "Listing services and span names registered in Tempo",
        "Correlating slow or error spans with logs and metrics",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_VALID_ACTIONS),
                "default": "search",
                "description": "Which Tempo query to run.",
            },
            "trace_id": {
                "type": "string",
                "description": "Trace ID to fetch (required when action='get_trace').",
            },
            "service": {"type": "string", "description": "Service name filter for search."},
            "span_name": {"type": "string", "description": "Span name filter for search."},
            "min_duration_ms": {"type": "number", "description": "Minimum span duration (ms)."},
            "max_duration_ms": {"type": "number", "description": "Maximum span duration (ms)."},
            "tags": {
                "type": "object",
                "description": "Span attribute filters (key -> value), applied as span.<key>.",
            },
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 20},
        },
        "required": [],
    },
    is_available=_tempo_is_available,
    extract_params=_tempo_extract_params,
)
def query_tempo(
    action: str = "search",
    trace_id: str | None = None,
    service: str | None = None,
    span_name: str | None = None,
    min_duration_ms: float | None = None,
    max_duration_ms: float | None = None,
    tags: dict[str, str] | None = None,
    time_range_minutes: int = 60,
    limit: int = 20,
    tempo_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query Grafana Tempo for traces, a single trace, or registered tag values."""
    if action not in _VALID_ACTIONS:
        action = "search"

    if tempo_backend is not None:
        return _dispatch(
            tempo_backend,
            action=action,
            trace_id=trace_id,
            service=service,
            span_name=span_name,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            tags=tags,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )

    config = TempoConfig.model_validate(_kwargs)
    if not config.is_configured:
        return {
            "source": "tempo",
            "action": action,
            "available": False,
            "error": "Tempo not configured. Provide TEMPO_URL.",
        }

    return _dispatch(
        TempoClient(config),
        action=action,
        trace_id=trace_id,
        service=service,
        span_name=span_name,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
        tags=tags,
        time_range_minutes=time_range_minutes,
        limit=limit,
    )
