"""SigNoz traces query tool."""

from __future__ import annotations

from typing import Any

from app.integrations.signoz import SigNozConfig, signoz_extract_params
from app.services.signoz.client import SigNozClient
from app.tools.tool_decorator import tool
from app.tools.utils.availability import signoz_available_or_backend


def _traces_is_available(sources: dict[str, dict]) -> bool:
    return signoz_available_or_backend(sources)


def _traces_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return {
        **signoz_extract_params(sources),
        "service": sources.get("signoz", {}).get("service_name", ""),
        "time_range_minutes": sources.get("signoz", {}).get("time_range_minutes", 60),
        "error_only": False,
        "limit": 50,
        "signoz_backend": sources.get("signoz", {}).get("_backend"),
    }


@tool(
    name="query_signoz_traces",
    display_name="SigNoz traces",
    source="signoz",
    tags=("traces", "observability"),
    cost_tier="moderate",
    description="Query SigNoz traces for error rate, latency, and slow spans.",
    use_cases=[
        "Investigating slow spans and error traces in SigNoz",
        "Finding p99 latency bottlenecks by service",
        "Correlating trace errors with logs and metrics",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Service name filter"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "error_only": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 50},
        },
        "required": [],
    },
    is_available=_traces_is_available,
    extract_params=_traces_extract_params,
)
def query_signoz_traces(
    service: str | None = None,
    time_range_minutes: int = 60,
    error_only: bool = False,
    limit: int = 50,
    signoz_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query SigNoz traces for error rate, latency, and slow spans."""
    if signoz_backend is not None:
        traces_result = signoz_backend.query_traces(
            service=service,
            time_range_minutes=time_range_minutes,
            error_only=error_only,
            limit=limit,
        )
        summary = signoz_backend.query_trace_summary(
            service=service,
            time_range_minutes=time_range_minutes,
        )
        return {
            **traces_result,
            "summary": summary,
        }

    config = SigNozConfig.model_validate(_kwargs)
    if not config.is_configured:
        return {
            "source": "signoz_traces",
            "available": False,
            "error": "SigNoz integration not configured",
            "traces": [],
        }

    client = SigNozClient(config)
    traces_result = client.query_traces(
        service=service,
        time_range_minutes=time_range_minutes,
        error_only=error_only,
        limit=limit,
    )
    summary = client.query_trace_summary(
        service=service,
        time_range_minutes=time_range_minutes,
    )
    return {
        **traces_result,
        "summary": summary,
    }
