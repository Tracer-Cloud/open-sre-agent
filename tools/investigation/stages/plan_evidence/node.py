"""Plan investigation actions from alert context and available tools."""

from __future__ import annotations

from typing import Any

from context.state import InvestigationState
from core.domain.alerts.alert_source import (
    primary_sources_for_alert,
    relevant_sources_for_alert,
)
from core.domain.alerts.tool_planning import FALLBACK_TOOL_NAMES, score_tools
from core.domain.types.planning import PlannedInvestigationAction
from core.domain.types.retrieval import RetrievalControlsMap, RetrievalIntent, TimeBounds
from tools.investigation.stages.gather_evidence.tools import (
    availability_view,
    build_connected_tool_context,
)
from tools.registered_tool import RegisteredTool
from tools.registry import get_registered_tools

DEFAULT_RETRIEVAL_LIMIT = 100


def plan_actions(state: InvestigationState) -> dict[str, Any]:
    """Return a prioritized investigation tool plan as partial state updates."""
    if state.get("is_noise"):
        return {}

    state_any = dict(state)
    raw_resolved = state_any.get("resolved_integrations")
    resolved = raw_resolved if isinstance(raw_resolved, dict) else {}
    available_tools = _available_investigation_tools(resolved)
    tool_context = build_connected_tool_context(resolved, available_tools)

    if not available_tools:
        return {
            "planned_actions": [],
            "plan_rationale": "No available investigation tools matched the resolved integrations.",
            "retrieval_controls": None,
            "plan_audit": {
                "selected": [],
                "excluded": [],
                "tool_budget": _tool_budget(state_any),
                "matched_sources": [],
                "primary_sources": list(primary_sources_for_alert(state_any)),
            },
            **tool_context,
        }

    scored = score_tools(state_any, available_tools)
    selected, excluded = _apply_budget(state_any, scored)
    retrieval_controls = _build_retrieval_controls(state_any, selected)

    selected_names = [action.name for action in selected]
    plan_rationale = _build_plan_rationale(state_any, selected)

    return {
        "planned_actions": selected_names,
        "plan_rationale": plan_rationale,
        "retrieval_controls": retrieval_controls or None,
        "plan_audit": {
            "selected": [_audit_entry(action) for action in selected],
            "excluded": [_audit_entry(action) for action in excluded],
            "tool_budget": _tool_budget(state_any),
            "matched_sources": _matched_sources(state_any, available_tools),
            "primary_sources": list(primary_sources_for_alert(state_any)),
        },
        **tool_context,
    }


def _available_investigation_tools(resolved_integrations: dict[str, Any]) -> list[RegisteredTool]:
    available_sources = availability_view(resolved_integrations)
    return [
        tool
        for tool in get_registered_tools("investigation")
        if tool.is_available(available_sources)
    ]


def _apply_budget(
    state: dict[str, Any],
    scored: list[PlannedInvestigationAction],
) -> tuple[list[PlannedInvestigationAction], list[PlannedInvestigationAction]]:
    positive = [action for action in scored if action.score > 0]
    fallback = [action for action in scored if action.name in FALLBACK_TOOL_NAMES]
    candidates = positive or fallback
    budget = _tool_budget(state)
    selected = candidates[:budget]
    excluded_candidates = candidates[budget:]
    not_candidates = [
        action for action in scored if action not in positive and action not in fallback
    ]
    return selected, excluded_candidates + not_candidates


def _tool_budget(state: dict[str, Any]) -> int:
    raw_budget = state.get("tool_budget", 10)
    try:
        return max(1, min(50, int(raw_budget)))
    except (TypeError, ValueError):
        return 10


def _build_retrieval_controls(
    state: dict[str, Any],
    selected: list[PlannedInvestigationAction],
    available_tools: list[RegisteredTool] | None = None,
) -> RetrievalControlsMap:
    if available_tools is None:
        available_tools = _available_investigation_tools(state.get("resolved_integrations") or {})
    tools_by_name = {tool.name: tool for tool in available_tools}
    intent_by_name: RetrievalControlsMap = {}
    for action in selected:
        tool = tools_by_name.get(action.name)
        if tool is None:
            continue
        intent = _retrieval_intent_for_tool(state, tool)
        if intent is not None and intent.has_controls():
            intent_by_name[action.name] = intent
    return intent_by_name


def _retrieval_intent_for_tool(
    state: dict[str, Any], tool: RegisteredTool
) -> RetrievalIntent | None:
    kwargs: dict[str, Any] = {}
    if tool.retrieval_controls.time_bounds:
        time_bounds = _time_bounds_from_state(state)
        if time_bounds is not None:
            kwargs["time_bounds"] = time_bounds
    if tool.retrieval_controls.limit:
        kwargs["limit"] = DEFAULT_RETRIEVAL_LIMIT
    return RetrievalIntent(**kwargs) if kwargs else None


def _time_bounds_from_state(state: dict[str, Any]) -> TimeBounds | None:
    incident_window = state.get("incident_window")
    if not isinstance(incident_window, dict):
        return None
    start = incident_window.get("start") or incident_window.get("since")
    end = incident_window.get("end") or incident_window.get("until")
    if not start and not end:
        return None
    return TimeBounds(
        start_time=str(start) if start else None,
        end_time=str(end) if end else None,
    )


def _build_plan_rationale(
    state: dict[str, Any],
    selected: list[PlannedInvestigationAction],
) -> str:
    if not selected:
        return "No confident investigation tool match was found."
    source_summary = ", ".join(sorted({action.source for action in selected}))
    alert_source = str(state.get("alert_source") or "unknown")
    return (
        f"Selected {len(selected)} tool(s) from {source_summary} for alert source "
        f"'{alert_source}', prioritized by source/context relevance and tool metadata."
    )


def _matched_sources(state: dict[str, Any], tools: list[RegisteredTool]) -> list[str]:
    return relevant_sources_for_alert(state, {str(tool.source) for tool in tools})


def _audit_entry(action: PlannedInvestigationAction) -> dict[str, Any]:
    return {
        "name": action.name,
        "source": action.source,
        "score": action.score,
        "reasons": list(action.reasons),
    }


__all__ = ["plan_actions"]
