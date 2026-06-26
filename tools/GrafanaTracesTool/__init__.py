"""Grafana Tempo trace query tool."""

from __future__ import annotations

from typing import Any

from core.domain.pipeline_spans import extract_pipeline_spans
from integrations.opensre.grafana_backend_queries import query_traces_from_backend
from platform.common.evidence_compaction import (
    DEFAULT_TRACE_LIMIT,
    compact_traces,
    summarize_counts,
)
from tools.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _grafana_source,
    _resolve_grafana_client,
)
from tools.tool_decorator import tool

# Backward-compat shim: this module historically defined
# ``_extract_pipeline_spans``. Existing tests/callers import the private
# name; the canonical implementation now lives in
# ``core.domain.pipeline_spans``.
_extract_pipeline_spans = extract_pipeline_spans


def _query_grafana_traces_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _grafana_source(sources)
    return {
        "service_name": grafana.get("service_name", ""),
        "execution_run_id": grafana.get("execution_run_id"),
        "limit": grafana.get("limit", DEFAULT_TRACE_LIMIT),
        "grafana_backend": grafana.get("_backend"),
        **_grafana_creds(grafana),
    }


def _query_grafana_traces_available(sources: dict[str, dict]) -> bool:
    # `no_traces` is set for RDS/database resource-threshold alerts (storage,
    # CPU, connections, IOPS) where Tempo contains no useful data. Removing the
    # action from the planner's choice set is more reliable than the soft prompt
    # prohibition — the LLM was observed picking traces anyway and burning the
    # trajectory_budget gate (see scenario
    # 008-storage-full-missing-metric).
    if _grafana_source(sources).get("no_traces"):
        return False
    return _grafana_available(sources)


@tool(
    name="query_grafana_traces",
    display_name="Grafana Tempo",
    source="grafana",
    description="Query Grafana Cloud Tempo for pipeline traces.",
    use_cases=[
        "Tracing distributed request flows during a pipeline failure",
        "Identifying slow spans or timeout patterns",
        "Correlating trace data with log errors",
    ],
    requires=["service_name"],
    input_schema={
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "execution_run_id": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": ["service_name"],
    },
    is_available=_query_grafana_traces_available,
    extract_params=_query_grafana_traces_extract_params,
)
def query_grafana_traces(
    service_name: str,
    execution_run_id: str | None = None,
    limit: int = 20,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana Cloud Tempo for pipeline traces."""
    if grafana_backend is not None:
        return query_traces_from_backend(
            grafana_backend,
            service_name=service_name,
            execution_run_id=execution_run_id,
            limit=limit,
            extract_pipeline_spans=_extract_pipeline_spans,
        )

    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
    if not client or not client.is_configured:
        return {
            "source": "grafana_tempo",
            "available": False,
            "error": "Grafana integration not configured",
            "traces": [],
        }
    if not client.tempo_datasource_uid:
        return {
            "source": "grafana_tempo",
            "available": False,
            "error": "Tempo datasource not found",
            "traces": [],
        }

    result = client.query_tempo(service_name, limit=limit)
    if not result.get("success"):
        return {
            "source": "grafana_tempo",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "traces": [],
        }

    traces = result.get("traces", [])
    if execution_run_id and traces:
        filtered = [
            t
            for t in traces
            if any(
                s.get("attributes", {}).get("execution.run_id") == execution_run_id
                for s in t.get("spans", [])
            )
        ]
        traces = filtered if filtered else traces

    # Compact traces to stay within prompt limits
    compacted_traces = compact_traces(traces, limit=limit)
    summary = summarize_counts(len(traces), len(compacted_traces), "traces")

    result_data = {
        "source": "grafana_tempo",
        "available": True,
        "traces": compacted_traces,
        "pipeline_spans": _extract_pipeline_spans(compacted_traces),
        "total_traces": result.get("total_traces", 0),
        "service_name": service_name,
        "execution_run_id": execution_run_id,
        "account_id": client.account_id,
    }
    if summary:
        result_data["truncation_note"] = summary
    return result_data
