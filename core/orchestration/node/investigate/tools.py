"""Tool resolution, seeding, and evidence helpers for the investigate node."""

from __future__ import annotations

from typing import Any

from core.domain.alerts.alert_source import (
    ALERT_SOURCE_TO_SEED_TOOL_SOURCES,
    resolve_alert_source,
)
from core.runtime import public_tool_input
from core.runtime.llm.agent_llm_client import ToolCall
from platform.observability.tool_trace import redact_sensitive
from tools.registered_tool import RegisteredTool
from tools.registry import get_registered_tools
from tools.utils.integration_sources import availability_view

# Consecutive iterations made up ENTIRELY of duplicate (already-seen) tool calls
# that we tolerate before forcing the agent to conclude.
MAX_STAGNANT_ITERATIONS = 2

# Injected as a user turn once the agent starts repeating itself.
STAGNATION_NUDGE = (
    "You are repeating tool calls you already made, so they return no new "
    "information and the investigation is not progressing. Stop calling tools and "
    "write your final diagnosis from the evidence already gathered: root cause, "
    "root cause category, supporting evidence, validated and non-validated claims, "
    "remediation steps, and a validity score. If the evidence is insufficient to "
    "determine a root cause, say so explicitly and use a low validity score."
)


def get_available_tools(resolved_integrations: dict[str, Any]) -> list[RegisteredTool]:
    available_sources = availability_view(resolved_integrations)
    return [t for t in get_registered_tools("investigation") if t.is_available(available_sources)]


def build_connected_tool_context(
    resolved_integrations: dict[str, Any],
    tools: list[RegisteredTool],
) -> dict[str, Any]:
    from pydantic import BaseModel

    from integrations.registry import family_key

    connected_integrations = sorted(
        key
        for key, value in resolved_integrations.items()
        if not key.startswith("_")
        and (isinstance(value, BaseModel) or (isinstance(value, dict) and value))
    )
    connected_families = {family_key(key) for key in connected_integrations}

    sources: dict[str, dict[str, Any]] = {}
    for tool in sorted(tools, key=lambda item: (str(item.source), item.name)):
        source = str(tool.source)
        source_info = sources.setdefault(
            source,
            {
                "connected": source in connected_integrations
                or family_key(source) in connected_families,
                "tools": [],
            },
        )
        source_info["tools"].append(tool.name)

    return {
        "connected_integrations": connected_integrations,
        "available_sources": sources,
        "available_action_names": [tool.name for tool in sorted(tools, key=lambda item: item.name)],
    }


def build_seed_calls(
    state: dict[str, Any],
    tools: list[RegisteredTool],
    llm: Any,
) -> list[ToolCall]:
    """Return tool calls to run before the LLM loop based on the alert source."""
    alert_source = get_alert_source(state)
    if not alert_source:
        return []

    target_sources = set(ALERT_SOURCE_TO_SEED_TOOL_SOURCES.get(alert_source, ()))
    if not target_sources:
        return []

    resolved = state.get("resolved_integrations") or {}
    tool_sources = availability_view(resolved)
    seed_tools = [t for t in tools if str(t.source) in target_sources]
    if not seed_tools:
        return []

    from core.runtime.llm.agent_llm_client import BedrockConverseAgentClient
    from core.runtime.llm.bedrock_converse import new_tool_use_id

    use_converse_ids = isinstance(llm, BedrockConverseAgentClient)
    calls: list[ToolCall] = []
    for tool in seed_tools:
        try:
            injected = tool.extract_params(tool_sources)
        except Exception:
            injected = {}
        tool_id = new_tool_use_id() if use_converse_ids else f"seed_{tool.name}"
        calls.append(ToolCall(id=tool_id, name=tool.name, input=public_tool_input(injected)))

    return calls


def get_alert_source(state: dict[str, Any]) -> str:
    return resolve_alert_source(state)


def tool_event_payload(tc: ToolCall, *, output: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": tc.id,
        "name": tc.name,
        "input": redact_sensitive(tc.input),
    }
    if output is not None:
        payload["output"] = redact_sensitive(output)
    return payload


def merge_tool_evidence(
    evidence: dict[str, Any],
    tool_name: str,
    output: Any,
    tool_input: dict[str, Any],
) -> None:
    """Store raw tool output and the legacy report-facing evidence keys."""
    evidence[tool_name] = output
    tool_outputs = evidence.setdefault("tool_outputs", [])
    if isinstance(tool_outputs, list):
        tool_outputs.append(
            {
                "tool_name": tool_name,
                "tool_args": redact_sensitive(tool_input),
                "data": redact_sensitive(output),
            }
        )

    if not isinstance(output, dict):
        return

    if tool_name == "query_grafana_logs":
        evidence["grafana_logs"] = output.get("logs", [])
        evidence["grafana_error_logs"] = output.get("error_logs", [])
        evidence["grafana_logs_query"] = output.get("query", "")
        evidence["grafana_logs_service"] = output.get("service_name", "")
        return

    if tool_name == "query_grafana_metrics":
        metric_name = str(output.get("metric_name") or tool_input.get("metric_name") or "")
        metric_results = evidence.setdefault("grafana_metric_results", {})
        if isinstance(metric_results, dict) and metric_name:
            metric_results[metric_name] = output
        evidence["grafana_metrics"] = output.get("metrics", [])
        return

    if tool_name == "query_grafana_traces":
        evidence["grafana_traces"] = output.get("traces", [])
        evidence["grafana_pipeline_spans"] = output.get("pipeline_spans", [])
        return

    if tool_name == "query_grafana_alert_rules":
        evidence["grafana_alert_rules"] = output.get("rules", [])
        return

    if tool_name == "query_grafana_service_names":
        evidence["grafana_service_names"] = output.get("service_names", [])
