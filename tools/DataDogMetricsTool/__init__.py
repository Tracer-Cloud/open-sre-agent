"""Datadog metrics query tool (stub — implementation pending)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from tools.tool_decorator import tool


class QueryDatadogMetricsInput(BaseModel):
    metric_name: str = Field(
        description="Datadog metric name to query, for example `system.cpu.user`."
    )
    time_range_minutes: int = Field(
        default=60,
        description="Lookback window in minutes for metric retrieval.",
    )
    query: str | None = Field(
        default=None,
        description="Optional full Datadog metrics query string override.",
    )


class QueryDatadogMetricsOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether Datadog metrics query is available.")
    metric_name: str = Field(description="Metric name requested.")
    metrics: list[dict[str, Any]] = Field(default_factory=list, description="Returned metric data.")
    error: str | None = Field(default=None, description="Error details when unavailable.")


def _metrics_is_available(_sources: dict[str, dict]) -> bool:
    # Hidden from the planner until the Metrics API v2 implementation lands (see #669).
    # Flip back to `bool(sources.get("datadog", {}).get("connection_verified"))` once
    # the stub body below is replaced with a real request.
    return False


def _metrics_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    dd = sources["datadog"]
    return {
        "metric_name": dd.get("metric_name", ""),
        "time_range_minutes": dd.get("time_range_minutes", 60),
        "api_key": dd.get("api_key"),
        "app_key": dd.get("app_key"),
        "site": dd.get("site", "datadoghq.com"),
    }


@tool(
    name="query_datadog_metrics",
    source="datadog",
    description="Query Datadog metrics for infrastructure and application performance data.",
    use_cases=[
        "Investigating CPU or memory spikes correlated with an alert",
        "Reviewing custom pipeline throughput metrics over time",
        "Checking host resource utilisation trends",
    ],
    requires=[],
    source_id="datadog_metrics_api",
    evidence_type="metrics",
    side_effect_level="read_only",
    examples=[
        "Check `system.cpu.user` around incident window for saturation patterns.",
        "Run a custom metrics query string for service-specific error-rate metrics.",
    ],
    anti_examples=["Use this tool for log content or deployment timeline evidence."],
    input_model=QueryDatadogMetricsInput,
    output_model=QueryDatadogMetricsOutput,
    injected_params=("api_key", "app_key", "site"),
    is_available=_metrics_is_available,
    extract_params=_metrics_extract_params,
)
def query_datadog_metrics(
    metric_name: str,
    time_range_minutes: int = 60,
    query: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query Datadog metrics for infrastructure and application performance data.

    NOTE: This tool is a stub. A full implementation will query the Datadog
    Metrics API (v2) to retrieve time-series data for pipeline performance,
    host resource utilisation, and custom business metrics.
    """
    return {
        "source": "datadog_metrics",
        "available": False,
        "error": "DataDogMetricsTool is not yet implemented.",
        "metric_name": metric_name,
        "time_range_minutes": time_range_minutes,
        "query": query,
        "metrics": [],
    }
