"""SigNoz log search tool."""

from __future__ import annotations

from typing import Any, cast

from integrations.signoz import SigNozConfig, signoz_extract_params
from services.signoz.client import SigNozClient
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
