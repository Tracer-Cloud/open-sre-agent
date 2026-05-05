"""Grafana alert rules query tool."""

from __future__ import annotations

from typing import Any

from app.tools.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _resolve_grafana_client,
)
from app.tools.tool_decorator import tool


def _query_grafana_alert_rules_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = sources.get("grafana", {})
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
