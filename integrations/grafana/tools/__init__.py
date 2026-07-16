# ======== from tools/grafana_alert_rules_tool/ ========

"""Grafana alert rules query tool."""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool


def _query_grafana_alert_rules_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _grafana_source(sources)
    return {
        "folder": grafana.get("pipeline_name"),
        "grafana_backend": grafana.get("_backend"),
        **_grafana_creds(grafana),
    }


def _query_grafana_alert_rules_available(sources: dict[str, dict]) -> bool:
    return _grafana_available(sources)


def _normalize_backend_alert_rules(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize fixture/backend ruler responses to the client rule shape."""
    rules: list[dict[str, Any]] = []
    for group in raw.get("groups", []):
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name", ""))
        folder = str(group.get("folder", ""))
        for rule in group.get("rules", []):
            if not isinstance(rule, dict):
                continue
            annotations = rule.get("annotations", {})
            labels = rule.get("labels", {})
            rules.append(
                {
                    "rule_name": rule.get("name") or rule.get("title") or "unknown",
                    "state": rule.get("state", ""),
                    "folder": folder,
                    "group": group_name,
                    "queries": rule.get("queries", []),
                    "labels": labels if isinstance(labels, dict) else {},
                    "annotations": annotations if isinstance(annotations, dict) else {},
                    "no_data_state": rule.get("no_data_state") or rule.get("noDataState"),
                }
            )
    return rules


@tool(
    name="query_grafana_alert_rules",
    display_name="Grafana alerts",
    source="grafana",
    description="Query Grafana alert rules to understand what is being monitored.",
    use_cases=[
        "Investigating DatasourceNoData alerts to find the exact PromQL/LogQL query",
        "Understanding monitoring configuration and thresholds",
        "Auditing which alerts are active for a pipeline",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "folder": {"type": "string"},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": [],
    },
    is_available=_query_grafana_alert_rules_available,
    extract_params=_query_grafana_alert_rules_extract_params,
)
def query_grafana_alert_rules(
    folder: str | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana alert rules to understand what is being monitored."""
    if grafana_backend is not None:
        raw = grafana_backend.query_alert_rules()
        rules = _normalize_backend_alert_rules(raw)
        return {
            "source": "grafana_alerts",
            "available": True,
            "rules": rules,
            "total_rules": len(rules),
            "raw": raw,
        }

    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
    if not client or not client.is_configured:
        return {
            "source": "grafana_alerts",
            "available": False,
            "error": "Grafana integration not configured",
            "rules": [],
        }

    rules = client.query_alert_rules(folder=folder)
    return {
        "source": "grafana_alerts",
        "available": True,
        "rules": rules,
        "total_rules": len(rules),
        "folder_filter": folder,
    }


# ======== from tools/grafana_annotations_tool/ ========

"""Grafana deployment-annotations query tool for change correlation."""


import time
from datetime import UTC, datetime

from core.tool_framework.tool_decorator import tool
from integrations.grafana.base import _epoch_ms_to_iso, _map_annotation


def _query_grafana_annotations_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _grafana_source(sources)
    return {
        "time_range_minutes": grafana.get("time_range_minutes", 60),
        "grafana_backend": grafana.get("_backend"),
        **_grafana_creds(grafana),
    }


def _query_grafana_annotations_available(sources: dict[str, dict]) -> bool:
    return _grafana_available(sources)


def _normalize_backend_annotations(raw: Any) -> list[dict[str, Any]]:
    """Normalize fixture/backend ``/api/annotations`` arrays to the client shape."""
    if not isinstance(raw, list):
        return []
    return [_map_annotation(item) for item in raw if isinstance(item, dict)]


def _iso_to_epoch_ms(value: str) -> int:
    """Parse an ISO 8601 timestamp to epoch milliseconds (UTC). Raises ValueError if invalid.

    A timezone-naive value (no ``Z`` / offset) is interpreted as UTC, not host-local time.
    """
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


@tool(
    name="query_grafana_annotations",
    display_name="Grafana annotations",
    source="grafana",
    description=(
        "Query Grafana deployment/config-change annotations to correlate changes with "
        "an incident — the source-agnostic 'what changed and when' marker."
    ),
    use_cases=[
        "Checking whether a deploy or config change preceded an alert",
        "Correlating incidents with ArgoCD/Flux/Helm/Terraform/manual changes emitted as annotations",
        "Building a source-agnostic change timeline alongside the GitHub deploy timeline",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "from": {
                "type": "string",
                "description": "ISO 8601 window start (overrides time_range_minutes)",
            },
            "to": {
                "type": "string",
                "description": "ISO 8601 window end (overrides time_range_minutes)",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 100},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": [],
    },
    is_available=_query_grafana_annotations_available,
    extract_params=_query_grafana_annotations_extract_params,
)
def query_grafana_annotations(
    tags: list[str] | None = None,
    time_range_minutes: int = 60,
    limit: int = 100,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana annotations to correlate deploys/config changes with an incident.

    ``from``/``to`` are accepted via the schema (ISO 8601); they are read from
    ``_kwargs`` because ``from`` is a Python keyword and cannot be a parameter name.
    When absent, the window defaults to the last ``time_range_minutes``.
    """
    if grafana_backend is not None:
        raw = grafana_backend.query_annotations(tags=tags, limit=limit)
        annotations = _normalize_backend_annotations(raw)
        return {
            "source": "grafana_annotations",
            "available": True,
            "annotations": annotations,
            "total": len(annotations),
            "raw": raw,
        }

    client = _resolve_grafana_client(
        grafana_endpoint, grafana_api_key, grafana_username, grafana_password
    )
    if not client or not client.is_configured:
        return {
            "source": "grafana_annotations",
            "available": False,
            "error": "Grafana integration not configured",
            "annotations": [],
        }

    now_ms = int(time.time() * 1000)
    try:
        from_iso, to_iso = _kwargs.get("from"), _kwargs.get("to")
        to_ts = _iso_to_epoch_ms(to_iso) if to_iso else now_ms
        # Default the window to end at `to` (now if unset), so a `to`-only call still
        # yields a valid [to - window, to] range rather than from_ts > to_ts.
        from_ts = _iso_to_epoch_ms(from_iso) if from_iso else to_ts - time_range_minutes * 60 * 1000
    except (ValueError, TypeError, AttributeError) as e:
        return {
            "source": "grafana_annotations",
            "available": False,
            "error": f"Invalid timestamp: {e}",
            "annotations": [],
        }

    annotations = client.query_annotations(from_ts=from_ts, to_ts=to_ts, tags=tags, limit=limit)
    return {
        "source": "grafana_annotations",
        "available": True,
        "annotations": annotations,
        "total": len(annotations),
        "tags_filter": tags,
        "from": _epoch_ms_to_iso(from_ts),
        "to": _epoch_ms_to_iso(to_ts),
    }


# ======== from tools/grafana_logs_tool/ ========

"""Grafana Loki log query tool — primary owner of Grafana helpers."""


from core.tool_framework.tool_decorator import tool
from integrations.grafana.client import get_grafana_client_from_credentials
from integrations.opensre.grafana_backend_queries import (
    query_logs_from_backend,
    query_metrics_from_backend,
    query_traces_from_backend,
)
from platform.common.evidence_compaction import summarize_counts
from platform.common.log_compaction import build_error_taxonomy, deduplicate_logs


def _resolve_grafana_client(
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
):
    if not grafana_endpoint:
        return None
    return get_grafana_client_from_credentials(
        endpoint=grafana_endpoint,
        api_key=grafana_api_key or "",
        username=grafana_username,
        password=grafana_password,
    )


def _grafana_creds(grafana: dict) -> dict:
    return {
        "grafana_endpoint": grafana.get("grafana_endpoint") or grafana.get("endpoint"),
        "grafana_api_key": grafana.get("grafana_api_key") or grafana.get("api_key"),
        "grafana_username": grafana.get("username", ""),
        "grafana_password": grafana.get("password", ""),
    }


def _grafana_source(sources: dict) -> dict:
    from pydantic import BaseModel

    grafana = sources.get("grafana") or sources.get("grafana_local") or {}
    if isinstance(grafana, BaseModel):
        item: dict[str, Any] = grafana.model_dump(exclude_none=True)
        item.setdefault("connection_verified", True)
        return item
    if isinstance(grafana, dict):
        if not grafana:
            return {}
        item = dict(grafana)
        item.setdefault("connection_verified", True)
        return item
    return {}


def _grafana_available(sources: dict) -> bool:
    grafana = _grafana_source(sources)
    return bool(
        grafana.get("connection_verified")
        or grafana.get("_backend")
        or grafana.get("grafana_endpoint")
        or grafana.get("endpoint")
    )


def _query_grafana_logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _grafana_source(sources)
    return {
        "service_name": grafana.get("service_name", ""),
        "pipeline_name": grafana.get("pipeline_name"),
        "execution_run_id": grafana.get("execution_run_id"),
        "time_range_minutes": grafana.get("time_range_minutes", 60),
        "limit": 100,
        "grafana_backend": grafana.get("_backend"),
        **_grafana_creds(grafana),
    }


def _query_grafana_logs_available(sources: dict[str, dict]) -> bool:
    return _grafana_available(sources)


@tool(
    name="query_grafana_logs",
    display_name="Grafana Loki",
    source="grafana",
    description="Query Grafana Loki for pipeline logs.",
    use_cases=[
        "Retrieving application logs from Grafana Loki during an incident",
        "Searching for error patterns in pipeline execution logs",
        "Correlating log events with Grafana alert triggers",
    ],
    requires=["service_name"],
    input_schema={
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "execution_run_id": {"type": "string"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 100},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
            "grafana_username": {"type": "string"},
            "grafana_password": {"type": "string"},
            "pipeline_name": {"type": "string"},
        },
        "required": ["service_name"],
    },
    is_available=_query_grafana_logs_available,
    extract_params=_query_grafana_logs_extract_params,
)
def query_grafana_logs(
    service_name: str,
    execution_run_id: str | None = None,
    time_range_minutes: int = 60,
    limit: int = 100,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    pipeline_name: str | None = None,
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana Loki for pipeline logs.

    Handles both injected test backends (FixtureGrafanaBackend) and real HTTP
    clients. When ``grafana_backend`` is present it is used directly; otherwise
    the tool falls back to the configured Grafana Cloud credentials.
    """
    if grafana_backend is not None:
        return query_logs_from_backend(
            grafana_backend,
            service_name=service_name,
            execution_run_id=execution_run_id,
        )

    client = _resolve_grafana_client(
        grafana_endpoint, grafana_api_key, grafana_username, grafana_password
    )
    if not client or not client.is_configured:
        return {
            "source": "grafana_loki",
            "available": False,
            "error": "Grafana integration not configured",
            "logs": [],
        }
    if not client.loki_datasource_uid:
        return {
            "source": "grafana_loki",
            "available": False,
            "error": "Loki datasource not found",
            "logs": [],
        }

    def _build_query(label: str, value: str) -> str:
        if execution_run_id:
            return f'{{{label}="{value}"}} |= "{execution_run_id}"'
        return f'{{{label}="{value}"}}'

    query = _build_query("service_name", service_name)
    result = client.query_loki(query, time_range_minutes=time_range_minutes, limit=limit)

    if result.get("success") and not result.get("logs") and pipeline_name:
        fallback_query = _build_query("pipeline_name", pipeline_name)
        fallback = client.query_loki(
            fallback_query, time_range_minutes=time_range_minutes, limit=limit
        )
        if fallback.get("success") and fallback.get("logs"):
            result = fallback
            query = fallback_query

    if not result.get("success"):
        return {
            "source": "grafana_loki",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "logs": [],
        }

    logs_data = result.get("logs", [])
    error_keywords = ("error", "fail", "exception", "traceback")
    error_logs = [
        log
        for log in logs_data
        if "error" in str(log.get("log_level", "")).lower()
        or any(kw in log.get("message", "").lower() for kw in error_keywords)
    ]

    # Phase 1: deduplicate + count-group so bursts don't steal all slots
    compacted_logs = deduplicate_logs(logs_data, max_output=50)
    compacted_error_logs = deduplicate_logs(error_logs, max_output=20)

    # Phase 2: structured error taxonomy across the *full* error set
    error_taxonomy = build_error_taxonomy(error_logs)

    result_data = {
        "source": "grafana_loki",
        "available": True,
        "logs": compacted_logs,
        "error_logs": compacted_error_logs,
        "total_logs": result.get("total_logs", 0),
        "compacted_log_count": len(compacted_logs),
        "compacted_error_log_count": len(compacted_error_logs),
        "error_taxonomy": error_taxonomy,
        "service_name": service_name,
        "execution_run_id": execution_run_id,
        "query": query,
        "account_id": client.account_id,
    }
    summary = summarize_counts(len(logs_data), len(compacted_logs), "logs")
    if summary:
        result_data["truncation_note"] = summary
    return result_data


# ======== from tools/grafana_metrics_tool/ ========

"""Grafana Mimir metrics query tool."""


from pydantic import BaseModel, Field

from core.tool_framework.tool_decorator import tool


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
        return query_metrics_from_backend(
            grafana_backend,
            metric_name=metric_name,
            service_name=service_name,
        )

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


# ======== from tools/grafana_service_names_tool/ ========

"""Grafana Loki service name discovery tool."""


from core.tool_framework.tool_decorator import tool


def _query_grafana_service_names_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _grafana_source(sources)
    return {
        **_grafana_creds(grafana),
        "grafana_backend": grafana.get("_backend"),
    }


def _query_grafana_service_names_available(sources: dict[str, dict]) -> bool:
    return _grafana_available(sources)


@tool(
    name="query_grafana_service_names",
    source="grafana",
    description="Discover available service names in Loki.",
    use_cases=[
        "Finding the correct service_name label when query_grafana_logs returns no results",
        "Listing all services that have log data in Grafana Loki",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": [],
    },
    is_available=_query_grafana_service_names_available,
    extract_params=_query_grafana_service_names_extract_params,
)
def query_grafana_service_names(
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Discover available service names in Loki."""
    if grafana_backend is not None:
        return {"source": "grafana_loki_labels", "available": True, "service_names": []}

    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
    if not client or not client.is_configured:
        return {
            "source": "grafana_loki_labels",
            "available": False,
            "error": "Grafana integration not configured",
            "service_names": [],
        }

    service_names = client.query_loki_label_values("service_name")
    return {
        "source": "grafana_loki_labels",
        "available": True,
        "service_names": service_names,
    }


# ======== from tools/grafana_traces_tool/ ========

"""Grafana Tempo trace query tool."""


from core.domain.pipeline_spans import extract_pipeline_spans as _extract_pipeline_spans
from core.tool_framework.tool_decorator import tool
from platform.common.evidence_compaction import DEFAULT_TRACE_LIMIT, compact_traces


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
