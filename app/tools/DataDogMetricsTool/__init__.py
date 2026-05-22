"""Datadog metrics query tool."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, Field

from app.tools.DataDogLogsTool import _dd_creds
from app.tools.DataDogLogsTool._client import make_client, unavailable
from app.tools.tool_decorator import tool
from app.tools.utils.availability import datadog_available_or_backend
from app.tools.utils.compaction import compact_metrics, summarize_counts

_AGGREGATION_PREFIXES = ("avg:", "sum:", "min:", "max:", "count:")
_MAX_SERIES = 20
_MAX_POINTS_PER_SERIES = 60


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
    query: str | None = Field(default=None, description="Datadog metrics query that was executed.")
    time_range_minutes: int = Field(default=60, description="Lookback window used.")
    total_series: int = Field(default=0, description="Number of timeseries returned.")
    metrics: list[dict[str, Any]] = Field(default_factory=list, description="Returned metric data.")
    truncation_note: str | None = Field(
        default=None, description="Notice when returned series are compacted."
    )
    window: dict[str, str] | None = Field(default=None, description="Query time window.")
    error: str | None = Field(default=None, description="Error details when unavailable.")


def _metrics_is_available(sources: dict[str, dict]) -> bool:
    if not datadog_available_or_backend(sources):
        return False

    dd = sources.get("datadog", {})
    backend = dd.get("_backend")
    # Real Datadog credentials can query metrics; fixture backends must expose the metrics API.
    return bool(dd.get("connection_verified") or hasattr(backend, "query_metrics"))


def _metrics_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    dd = sources["datadog"]
    return {
        "metric_name": dd.get("metric_name") or dd.get("default_metric") or "system.cpu.user",
        "query": dd.get("metric_query"),
        "time_range_minutes": dd.get("time_range_minutes", 60),
        "datadog_backend": dd.get("_backend"),
        **_dd_creds(dd),
    }


def _datadog_metric_query(metric_name: str, query: str | None) -> str:
    query_override = (query or "").strip()
    if query_override:
        return query_override

    metric = metric_name.strip()
    if not metric:
        return ""
    if metric.startswith(_AGGREGATION_PREFIXES):
        if "{" in metric and "}" in metric:
            return metric
        return f"{metric}{{*}}"
    if "{" in metric and "}" in metric:
        return f"avg:{metric}"
    return f"avg:{metric}{{*}}"


def _round_metric_value(value: float) -> float:
    return round(value, 4)


def _trend(first: float, latest: float) -> str:
    if latest > first:
        return "increased"
    if latest < first:
        return "decreased"
    return "flat"


def _summarize_values(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {}

    first = float(values[0])
    latest = float(values[-1])
    minimum = min(values)
    maximum = max(values)
    average = sum(values) / len(values)
    summary: dict[str, Any] = {
        "first": _round_metric_value(first),
        "latest": _round_metric_value(latest),
        "min": _round_metric_value(minimum),
        "max": _round_metric_value(maximum),
        "avg": _round_metric_value(average),
        "delta": _round_metric_value(latest - first),
        "trend": _trend(first, latest),
    }
    if first != 0:
        summary["delta_pct"] = _round_metric_value(((latest - first) / abs(first)) * 100)
    else:
        summary["delta_pct"] = None
    return summary


def _format_metric_series(
    series: list[dict[str, Any]],
    *,
    fallback_metric_name: str,
) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for item in series:
        if not isinstance(item, dict):
            continue

        raw_values = item.get("values") or []
        values = [float(value) for value in raw_values if isinstance(value, int | float)]
        metric_name = str(item.get("metric") or fallback_metric_name)
        metrics.append(
            {
                "metric_name": metric_name,
                "scope": item.get("scope", ""),
                "tags": item.get("tags", []),
                "unit": item.get("unit"),
                "point_count": int(item.get("point_count", len(values)) or 0),
                "points": item.get("points", []),
                "summary": _summarize_values(values),
            }
        )
    return metrics


@tool(
    name="query_datadog_metrics",
    display_name="Datadog metrics",
    source="datadog",
    tags=("metrics", "observability"),
    cost_tier="moderate",
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
    injected_params=("api_key", "app_key", "site", "datadog_backend"),
    is_available=_metrics_is_available,
    extract_params=_metrics_extract_params,
)
def query_datadog_metrics(
    metric_name: str,
    time_range_minutes: int = 60,
    query: str | None = None,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    datadog_backend: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Query Datadog metrics for infrastructure and application performance data."""
    query_to_run = _datadog_metric_query(metric_name, query)
    if not query_to_run:
        return unavailable(
            "datadog_metrics",
            "metrics",
            "A Datadog metric name or query is required",
            metric_name=metric_name,
            query=query,
            time_range_minutes=time_range_minutes,
        )
    if time_range_minutes < 1:
        return unavailable(
            "datadog_metrics",
            "metrics",
            "time_range_minutes must be at least 1",
            metric_name=metric_name,
            query=query_to_run,
            time_range_minutes=time_range_minutes,
        )

    if datadog_backend is not None and hasattr(datadog_backend, "query_metrics"):
        return cast(
            "dict[str, Any]",
            datadog_backend.query_metrics(
                metric_name=metric_name,
                query=query_to_run,
                time_range_minutes=time_range_minutes,
                **kwargs,
            ),
        )

    client = make_client(api_key, app_key, site)
    if not client:
        return unavailable(
            "datadog_metrics",
            "metrics",
            "Datadog integration not configured",
            metric_name=metric_name,
            query=query_to_run,
            time_range_minutes=time_range_minutes,
        )

    end = datetime.now(UTC)
    start = end - timedelta(minutes=time_range_minutes)
    result = client.query_metrics(query_to_run, start=start, end=end)
    if not result.get("success"):
        return unavailable(
            "datadog_metrics",
            "metrics",
            result.get("error", "Unknown error"),
            metric_name=metric_name,
            query=query_to_run,
            time_range_minutes=time_range_minutes,
        )

    series = result.get("series")
    if not isinstance(series, list):
        series = [
            {
                "metric": metric_name,
                "timestamps": result.get("timestamps") or [],
                "values": result.get("values") or [],
                "points": [
                    {"timestamp": timestamp, "value": value}
                    for timestamp, value in zip(
                        result.get("timestamps") or [],
                        result.get("values") or [],
                        strict=False,
                    )
                ],
            }
        ]

    metrics = _format_metric_series(series, fallback_metric_name=metric_name)
    compacted_metrics = compact_metrics(
        metrics,
        limit=_MAX_SERIES,
        max_datapoints=_MAX_POINTS_PER_SERIES,
    )
    total_series = int(result.get("total_series", len(metrics)) or 0)
    result_data: dict[str, Any] = {
        "source": "datadog_metrics",
        "available": True,
        "metric_name": metric_name,
        "time_range_minutes": time_range_minutes,
        "query": query_to_run,
        "metrics": compacted_metrics,
        "total_series": total_series,
        "window": {
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
        },
    }
    summary = summarize_counts(total_series, len(compacted_metrics), "metric series")
    if summary:
        result_data["truncation_note"] = summary
    return result_data
