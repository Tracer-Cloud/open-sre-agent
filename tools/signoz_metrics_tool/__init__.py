"""SigNoz metrics query tool."""

from __future__ import annotations

from typing import Any, cast

from integrations.signoz import SigNozConfig, signoz_extract_params
from services.signoz.client import SigNozClient
from tools.tool_decorator import tool
from tools.utils.availability import signoz_available_or_backend


def _metrics_is_available(sources: dict[str, dict]) -> bool:
    if signoz_available_or_backend(sources):
        return True
    signoz = sources.get("signoz", {})
    return bool(signoz.get("url") and signoz.get("api_key"))


def _metrics_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return {
        **signoz_extract_params(sources),
        "metric_name": "cpu_usage",
        "service": sources.get("signoz", {}).get("service_name", ""),
        "time_range_minutes": sources.get("signoz", {}).get("time_range_minutes", 60),
        "aggregation": "avg",
        "limit": 50,
        "signoz_backend": sources.get("signoz", {}).get("_backend"),
    }


@tool(
    name="query_signoz_metrics",
    display_name="SigNoz metrics",
    source="signoz",
    tags=("metrics", "observability"),
    cost_tier="moderate",
    description=("Query SigNoz metrics (CPU, memory, request rate) by service and time window."),
    use_cases=[
        "Checking CPU and memory usage from SigNoz metrics",
        "Reviewing request throughput by service",
        "Correlating metric anomalies with SigNoz alerts",
    ],
    requires=["metric_name"],
    input_schema={
        "type": "object",
        "properties": {
            "metric_name": {
                "type": "string",
                "description": (
                    "Metric name: cpu_usage, memory_usage, request_rate, "
                    "or a raw metric name. For error-rate semantics use "
                    "query_signoz_traces instead."
                ),
            },
            "service": {"type": "string", "description": "Service name filter"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "aggregation": {
                "type": "string",
                "default": "avg",
                "description": "avg, sum, max, min, count",
            },
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["metric_name"],
    },
    is_available=_metrics_is_available,
    extract_params=_metrics_extract_params,
)
def query_signoz_metrics(
    metric_name: str,
    service: str | None = None,
    time_range_minutes: int = 60,
    aggregation: str = "avg",
    limit: int = 50,
    signoz_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query SigNoz metrics by service and time window."""
    if signoz_backend is not None:
        return cast(
            "dict[str, Any]",
            signoz_backend.query_metrics(
                metric_name=metric_name,
                service=service,
                time_range_minutes=time_range_minutes,
                aggregation=aggregation,
                limit=limit,
            ),
        )

    config = SigNozConfig.model_validate(_kwargs)
    if not config.is_configured:
        return {
            "source": "signoz_metrics",
            "available": False,
            "error": ("SigNoz metrics not configured. Provide SIGNOZ_URL and SIGNOZ_API_KEY."),
            "metrics": [],
        }

    client = SigNozClient(config)
    return client.query_metrics(
        metric_name=metric_name,
        service=service,
        time_range_minutes=time_range_minutes,
        aggregation=aggregation,
        limit=limit,
    )
