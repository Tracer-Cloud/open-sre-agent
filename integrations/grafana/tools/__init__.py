"""Grafana agent tools — one package per tool, re-exported here.

Registration happens by ``@tool`` decorator at import time. The registry
discovers each tool package directly (``tools.registry_discovery`` walks this
package), so these re-exports exist for callers and tests that import from
``integrations.grafana.tools``, not to register anything.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import EvidenceType, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable

_GRAFANA_RUNTIME_PARAMS = (
    "grafana_endpoint",
    "grafana_api_key",
    "grafana_username",
    "grafana_password",
    "grafana_verify_ssl",
    "grafana_ca_bundle",
    "grafana_backend",
)


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


def _map_grafana_alert_rules(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    rules = output.get("rules", [])
    evidence["grafana_alert_rules"] = rules
    if rules:
        record_evidence_entry(
            evidence,
            source="grafana_alert_rules",
            label="Grafana Alert Rules",
            summary=f"{len(rules)} rules",
        )


@tool(
    name="query_grafana_alert_rules",
    display_name="Grafana alerts",
    source="grafana",
    evidence_mapper=_map_grafana_alert_rules,
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
    injected_params=_GRAFANA_RUNTIME_PARAMS,
    is_available=_query_grafana_alert_rules_available,
    extract_params=_query_grafana_alert_rules_extract_params,
)
def query_grafana_alert_rules(
    folder: str | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    grafana_verify_ssl: bool = True,
    grafana_ca_bundle: str = "",
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

    client = _resolve_grafana_client(
        grafana_endpoint,
        grafana_api_key,
        grafana_username,
        grafana_password,
        grafana_verify_ssl,
        grafana_ca_bundle,
    )
    if not client or not client.is_configured:
        return tool_unavailable("grafana_alerts", "Grafana integration not configured", rules=[])

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

from core.tool_framework import tool
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


def _map_grafana_annotations(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    annotations = output.get("annotations", [])
    if annotations:
        evidence["grafana_annotations"] = annotations
        record_evidence_entry(
            evidence,
            source="grafana_annotations",
            label="Grafana Annotations",
            summary=f"{len(annotations)} annotations",
        )




@tool(
    name="query_grafana_annotations",
    display_name="Grafana annotations",
    source="grafana",
    evidence_mapper=_map_grafana_annotations,
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
    injected_params=_GRAFANA_RUNTIME_PARAMS,
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
    grafana_verify_ssl: bool = True,
    grafana_ca_bundle: str = "",
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
        grafana_endpoint,
        grafana_api_key,
        grafana_username,
        grafana_password,
        grafana_verify_ssl,
        grafana_ca_bundle,
    )
    if not client or not client.is_configured:
        return tool_unavailable(
            "grafana_annotations", "Grafana integration not configured", annotations=[]
        )

    now_ms = int(time.time() * 1000)
    try:
        from_iso, to_iso = _kwargs.get("from"), _kwargs.get("to")
        to_ts = _iso_to_epoch_ms(to_iso) if to_iso else now_ms
        # Default the window to end at `to` (now if unset), so a `to`-only call still
        # yields a valid [to - window, to] range rather than from_ts > to_ts.
        from_ts = _iso_to_epoch_ms(from_iso) if from_iso else to_ts - time_range_minutes * 60 * 1000
    except (ValueError, TypeError, AttributeError) as e:
        return tool_unavailable("grafana_annotations", f"Invalid timestamp: {e}", annotations=[])

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


from core.tool_framework import tool
from infrastructure.evidence.evidence_compaction import summarize_counts
from infrastructure.evidence.log_compaction import build_error_taxonomy, deduplicate_logs
from integrations.grafana.client import get_grafana_client_from_credentials
from integrations.grafana.tools._helpers import (
    GRAFANA_RUNTIME_PARAMS,
    _grafana_available,
    _grafana_creds,
    _grafana_source,
    _resolve_grafana_client,
)
from integrations.grafana.tools.grafana_alert_rules_tool import query_grafana_alert_rules
from integrations.grafana.tools.grafana_annotations_tool import (
    _iso_to_epoch_ms,
    query_grafana_annotations,
)
from integrations.grafana.tools.grafana_logs_tool import query_grafana_logs
from integrations.grafana.tools.grafana_metrics_tool import (
    QueryGrafanaMetricsInput,
    QueryGrafanaMetricsOutput,
    query_grafana_metrics,
)
from integrations.grafana.tools.grafana_service_names_tool import query_grafana_service_names
from integrations.grafana.tools.grafana_traces_tool import query_grafana_traces

# Alias kept for older call sites / tests that used the private constant name.
_GRAFANA_RUNTIME_PARAMS = GRAFANA_RUNTIME_PARAMS

__all__ = [
    "GRAFANA_RUNTIME_PARAMS",
    "QueryGrafanaMetricsInput",
    "QueryGrafanaMetricsOutput",
    "_GRAFANA_RUNTIME_PARAMS",
    "_grafana_available",
    "_grafana_creds",
    "_grafana_source",
    "_iso_to_epoch_ms",
    "_resolve_grafana_client",
    "get_grafana_client_from_credentials",
    "query_grafana_alert_rules",
    "query_grafana_annotations",
    "query_grafana_logs",
    "query_grafana_metrics",
    "query_grafana_service_names",
    "query_grafana_traces",
]
