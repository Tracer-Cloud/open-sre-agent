"""Grafana Mimir metrics query tool."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from tools.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _grafana_source,
    _resolve_grafana_client,
)
from tools.tool_decorator import tool


class QueryGrafanaMetricsInput(BaseModel):
    metric_name: str = Field(
        description="Grafana Mimir metric query expression to execute.",
        examples=["pipeline_runs_total", "sum(rate(http_requests_total[5m]))"],
    )
    service_name: str | None = Field(
        default=None,
        description="Optional service filter applied by Grafana helper query wrappers.",
    )


class QueryGrafanaMetricsOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether Grafana query execution succeeded.")
    metric_name: str = Field(description="Metric query string that was executed.")
    service_name: str | None = Field(default=None, description="Service filter used for the query.")
    total_series: int = Field(default=0, description="Number of timeseries returned.")
    metrics: list[dict[str, Any]] = Field(default_factory=list, description="Raw metrics payload.")
    error: str | None = Field(default=None, description="Error details when query fails.")
    account_id: int | None = Field(default=None, description="Grafana account id when available.")


def _query_grafana_metrics_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _grafana_source(sources)
    return {
        "metric_name": "pipeline_runs_total",
        "service_name": grafana.get("service_name"),
        "grafana_backend": grafana.get("_backend"),
        **_grafana_creds(grafana),
    }


def _query_grafana_metrics_available(sources: dict[str, dict]) -> bool:
    return _grafana_available(sources)


@tool(
    name="query_grafana_metrics",
    display_name="Grafana Mimir",
    source="grafana",
    description="Query Grafana Cloud Mimir for pipeline metrics.",
    use_cases=[
        "Checking pipeline throughput and error rate metrics",
        "Reviewing resource utilisation trends over time",
        "Correlating metric anomalies with alert triggers",
    ],
    requires=["metric_name"],
    source_id="grafana_mimir",
    evidence_type="metrics",
    side_effect_level="read_only",
    examples=[
        "Query `pipeline_runs_total` to verify throughput drops.",
        "Query HTTP error rate metric with a `service_name` filter.",
    ],
    anti_examples=["Use this tool for pod logs or deployment status."],
    input_model=QueryGrafanaMetricsInput,
    output_model=QueryGrafanaMetricsOutput,
    injected_params=(
        "grafana_endpoint",
        "grafana_api_key",
        "grafana_username",
        "grafana_password",
        "grafana_backend",
    ),
    is_available=_query_grafana_metrics_available,
    extract_params=_query_grafana_metrics_extract_params,
)
def query_grafana_metrics(
    metric_name: str,
    service_name: str | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana Cloud Mimir for pipeline metrics."""
    if grafana_backend is not None:
        raw = grafana_backend.query_timeseries(query=metric_name)
        metrics = raw.get("data", {}).get("result", [])
        return {
            "source": "grafana_mimir",
            "available": True,
            "metrics": metrics,
            "total_series": len(metrics),
            "metric_name": metric_name,
            "service_name": service_name,
        }

    client = _resolve_grafana_client(
        grafana_endpoint, grafana_api_key, grafana_username, grafana_password
    )
    if not client or not client.is_configured:
        return {
            "source": "grafana_mimir",
            "available": False,
            "error": "Grafana integration not configured",
            "metrics": [],
        }
    if not client.mimir_datasource_uid:
        return {
            "source": "grafana_mimir",
            "available": False,
            "error": "Mimir datasource not found",
            "metrics": [],
        }

    result = client.query_mimir(metric_name, service_name=service_name)
    if not result.get("success"):
        return {
            "source": "grafana_mimir",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "metrics": [],
        }

    return {
        "source": "grafana_mimir",
        "available": True,
        "metrics": result.get("metrics", []),
        "total_series": result.get("total_series", 0),
        "metric_name": metric_name,
        "service_name": service_name,
        "account_id": client.account_id,
    }
