"""Build integration inputs for investigation seed calls."""

from __future__ import annotations

from typing import Any

from core.domain.alerts.alert_source import seed_tool_sources_for_alert
from core.tool import RegisteredTool, availability_view


def build_seed_tool_sources(
    state: dict[str, Any], tools: list[RegisteredTool]
) -> tuple[set[str], dict[str, dict[str, Any]]]:
    """Overlay public alert fields on integration defaults for seed calls."""
    target_sources = set(seed_tool_sources_for_alert(state))
    if not target_sources:
        return target_sources, {}

    resolved = state.get("resolved_integrations") or {}
    tool_sources = availability_view(resolved)
    raw_alert = state.get("raw_alert") or {}
    alert_json = state.get("alert_json") or {}
    alert_context = {
        **(raw_alert if isinstance(raw_alert, dict) else {}),
        **(alert_json if isinstance(alert_json, dict) else {}),
    }
    for source in target_sources:
        source_config = tool_sources.get(source)
        if not isinstance(source_config, dict):
            continue
        public_fields = {
            key
            for tool in tools
            if str(tool.source) == source
            for key in tool.public_input_schema.get("properties", {})
        }
        overrides = {
            key: alert_context[key]
            for key in public_fields
            if key in alert_context and alert_context[key] is not None
        }
        if overrides:
            tool_sources = {
                **tool_sources,
                source: {**source_config, **overrides},
            }

    if "kubernetes" in tool_sources and alert_json:
        kubernetes = dict(tool_sources["kubernetes"])
        if alert_json.get("kube_namespace"):
            kubernetes["namespace"] = alert_json["kube_namespace"]
        if alert_json.get("pod_name"):
            kubernetes["pod_name"] = alert_json["pod_name"]
        tool_sources = {**tool_sources, "kubernetes": kubernetes}

    return target_sources, tool_sources
