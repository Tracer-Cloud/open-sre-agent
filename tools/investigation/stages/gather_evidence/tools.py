"""Tool resolution, seeding, and evidence helpers for the investigate node."""

from __future__ import annotations

from typing import Any

from core import public_tool_input
from core.domain.alerts.alert_source import (
    SECONDARY_TOOL_SOURCES,
    primary_sources_for_alert,
    relevant_sources_for_alert,
    resolve_alert_source,
    seed_tool_sources_for_alert,
)
from core.llm.types import ToolCall
from core.tool_framework.registered_tool import RegisteredTool
from core.tool_framework.utils.integration_sources import availability_view
from platform.observability.tool_trace import redact_sensitive
from tools.registry import get_registered_tools

# Consecutive iterations made up ENTIRELY of duplicate (already-seen) tool calls
# that we tolerate before forcing the agent to conclude.
MAX_STAGNANT_ITERATIONS = 2

# Upper bound on how many tool schemas we hand the model on a single turn. The
# whole registry (100+ tools across every connected integration) serialized into
# the request is the dominant pre-tool-call context cost. We never ship more than
# this; when more tools are available we keep the most alert-relevant ones first.
MAX_AGENT_TOOL_SCHEMAS = 32

# Slots reserved within ``MAX_AGENT_TOOL_SCHEMAS`` for cheap secondary/knowledge
# reasoning tools so they survive the cap on a busy environment. Kept small so
# incident-specific tools still dominate the budget.
MAX_SECONDARY_FALLBACK_TOOLS = 3

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


def planned_action_names(state: dict[str, Any]) -> list[str]:
    """Tool names selected by the planning stage, normalized and de-blanked."""
    planned_raw = state.get("planned_actions")
    if not isinstance(planned_raw, list):
        return []
    return [str(name).strip() for name in planned_raw if str(name).strip()]


def select_investigation_tools(
    tools: list[RegisteredTool],
    state: dict[str, Any],
    *,
    max_tools: int = MAX_AGENT_TOOL_SCHEMAS,
) -> list[RegisteredTool]:
    """Narrow the available tools to the relevant set handed to the model.

    This is the single source of truth shared by the agent (which serializes the
    result into tool schemas) and the prompt builder (which lists the same tools
    for orientation). Order of preference:

    1. An explicit ``planned_actions`` set from the planning stage — those tools
       were already scored for relevance, so use exactly them.
    2. Otherwise rank every available tool by how relevant its integration source
       is to the alert and keep the top ``max_tools``. A few slots are reserved
       for cheap knowledge/secondary reasoning tools so they survive the cap.

    The result never exceeds ``max_tools`` (a hard ceiling — the whole point of
    the budget). When the available set already fits the input is returned
    unchanged (ordering preserved), so the common single/few-integration case is
    unaffected; the cap only bites when many integrations are connected at once.
    """
    planned = _planned_subset(tools, state)
    if planned is not None:
        return planned
    if len(tools) <= max_tools:
        return tools

    ranked = _relevance_ranked(tools, state)
    secondary = [tool for tool in ranked if str(tool.source) in SECONDARY_TOOL_SOURCES]
    # Reserve a few slots *inside* the cap for cheap reasoning fallbacks so the
    # agent never loses its "reason about the alert" path on a busy environment,
    # without ever pushing the total past the hard ceiling.
    reserve = min(len(secondary), MAX_SECONDARY_FALLBACK_TOOLS)
    primary_budget = max(max_tools - reserve, 0)

    kept: list[RegisteredTool] = []
    kept_names: set[str] = set()
    for tool in ranked:
        if str(tool.source) in SECONDARY_TOOL_SOURCES or len(kept) >= primary_budget:
            continue
        kept.append(tool)
        kept_names.add(tool.name)
    for tool in secondary:
        if len(kept) >= max_tools:
            break
        if tool.name not in kept_names:
            kept.append(tool)
            kept_names.add(tool.name)
    return kept


def _planned_subset(
    tools: list[RegisteredTool], state: dict[str, Any]
) -> list[RegisteredTool] | None:
    names = planned_action_names(state)
    if not names:
        return None
    by_name = {tool.name: tool for tool in tools}
    chosen = [by_name[name] for name in names if name in by_name]
    # If none of the planned names resolve (stale/hallucinated plan), fall through
    # to relevance ranking rather than silently shipping the whole registry.
    return chosen or None


def _relevance_ranked(tools: list[RegisteredTool], state: dict[str, Any]) -> list[RegisteredTool]:
    sources_present = {str(tool.source) for tool in tools}
    primary = set(primary_sources_for_alert(state))
    content_relevant = set(relevant_sources_for_alert(state, sources_present))

    def rank(tool: RegisteredTool) -> tuple[int, str, str]:
        source = str(tool.source)
        if source in SECONDARY_TOOL_SOURCES:
            # Cheap reasoning fallbacks (knowledge, etc.): keep but never crowd
            # out incident-specific tools.
            tier = 3
        elif source in primary:
            tier = 0
        elif source in content_relevant:
            tier = 1
        else:
            tier = 2
        return (tier, source, tool.name)

    return sorted(tools, key=rank)


def build_connected_tool_context(
    resolved_integrations: dict[str, Any],
    tools: list[RegisteredTool],
) -> dict[str, Any]:
    from pydantic import BaseModel

    # ``family_key`` is a platform-level seam (populated by the ``integrations``
    # layer at import time). Importing it from ``platform.common`` keeps this
    # module free of ``tools -> integrations`` edges (T-4 layering audit,
    # issue #3352, item 27).
    from platform.common.service_families import family_key

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
    target_sources = set(seed_tool_sources_for_alert(state))
    if not target_sources:
        return []

    resolved = state.get("resolved_integrations") or {}
    tool_sources = availability_view(resolved)
    seed_tools = [t for t in tools if str(t.source) in target_sources]
    if not seed_tools:
        return []

    from core.llm.transports.sdk.agent_clients import BedrockConverseAgentClient
    from core.llm.transports.sdk.bedrock_converse import new_tool_use_id

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
