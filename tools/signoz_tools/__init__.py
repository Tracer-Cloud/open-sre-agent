# ======== from tools/signoz_logs_tool/ ========

"""SigNoz log search tool."""

from __future__ import annotations

from typing import Any, cast

from integrations.signoz import SigNozConfig, signoz_extract_params
from integrations.signoz.client import SigNozClient
from tools.tool_decorator import tool
from tools.utils.availability import signoz_available_or_backend
from tools.utils.compaction import compact_logs, summarize_counts


def _logs_is_available(sources: dict[str, dict]) -> bool:
    if signoz_available_or_backend(sources):
        return True
    signoz = sources.get("signoz", {})
    return bool(signoz.get("url") and signoz.get("api_key"))


def _logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return {
        **signoz_extract_params(sources),
        "service": sources.get("signoz", {}).get("service_name", ""),
        "time_range_minutes": sources.get("signoz", {}).get("time_range_minutes", 60),
        "limit": 50,
        "signoz_backend": sources.get("signoz", {}).get("_backend"),
    }


def _normalize_logs_payload(
    result: dict[str, Any],
    *,
    service: str | None,
) -> dict[str, Any]:
    """Normalize logs output to the canonical envelope expected by the agent."""
    if not result.get("available"):
        return result

    logs = result.get("logs", [])
    error_keywords = ("error", "fail", "exception", "traceback", "panic", "fatal")
    error_logs = [
        log
        for log in logs
        if log.get("severity", "").upper() in ("ERROR", "FATAL", "CRITICAL")
        or any(kw in log.get("message", "").lower() for kw in error_keywords)
    ]

    compacted_logs = compact_logs(logs, limit=50)
    compacted_error_logs = compact_logs(error_logs, limit=30)

    result_data = {
        "source": "signoz_logs",
        "available": True,
        "logs": compacted_logs,
        "error_logs": compacted_error_logs,
        "total": result.get("total", 0),
        "service": service,
    }
    summary = summarize_counts(result.get("total", 0), len(compacted_logs), "logs")
    if summary:
        result_data["truncation_note"] = summary
    return result_data


@tool(
    name="query_signoz_logs",
    display_name="SigNoz logs",
    source="signoz",
    tags=("logs", "observability"),
    cost_tier="moderate",
    description="Query SigNoz logs by service, severity, and time window.",
    use_cases=[
        "Investigating application errors reported by SigNoz alerts",
        "Searching for error logs by service name and severity",
        "Correlating log events with SigNoz trace spans",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Service name filter"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "severity": {"type": "string", "description": "Severity filter (e.g. ERROR, WARN)"},
            "limit": {"type": "integer", "default": 50},
        },
        "required": [],
    },
    is_available=_logs_is_available,
    extract_params=_logs_extract_params,
)
def query_signoz_logs(
    service: str | None = None,
    time_range_minutes: int = 60,
    severity: str | None = None,
    limit: int = 50,
    signoz_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query SigNoz logs by service, severity, and time window."""
    if signoz_backend is not None:
        backend_result = cast(
            "dict[str, Any]",
            signoz_backend.query_logs(
                service=service,
                time_range_minutes=time_range_minutes,
                severity=severity,
                limit=limit,
            ),
        )
        return _normalize_logs_payload(backend_result, service=service)

    config = SigNozConfig.model_validate(_kwargs)
    if not config.is_configured:
        return {
            "source": "signoz_logs",
            "available": False,
            "error": "SigNoz logs not configured. Provide SIGNOZ_URL and SIGNOZ_API_KEY.",
            "logs": [],
        }

    client = SigNozClient(config)
    result = client.query_logs(
        service=service,
        time_range_minutes=time_range_minutes,
        severity=severity,
        limit=limit,
    )
    return _normalize_logs_payload(result, service=service)


# ======== from tools/signoz_metrics_tool/ ========

"""SigNoz metrics query tool."""


from tools.tool_decorator import tool


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


# ======== from tools/signoz_traces_tool/ ========

"""SigNoz traces query tool."""


from tools.tool_decorator import tool


def _traces_is_available(sources: dict[str, dict]) -> bool:
    if signoz_available_or_backend(sources):
        return True
    signoz = sources.get("signoz", {})
    return bool(signoz.get("url") and signoz.get("api_key"))


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
            "error": "SigNoz traces not configured. Provide SIGNOZ_URL and SIGNOZ_API_KEY.",
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
