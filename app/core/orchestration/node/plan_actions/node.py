"""Plan investigation actions from alert context and available tools."""

from __future__ import annotations

from typing import Any

from app.agent.utils.alert_source import (
    SECONDARY_TOOL_SOURCES as SECONDARY_SOURCES,
)
from app.agent.utils.alert_source import (
    collect_alert_text,
    primary_sources_for_alert,
    relevant_sources_for_alert,
)
from app.core.orchestration.node.investigate.tools import (
    availability_view,
    build_connected_tool_context,
)
from app.core.orchestration.node.plan_actions.models import PlannedInvestigationAction
from app.state import InvestigationState
from app.tools.registered_tool import RegisteredTool
from app.tools.registry import get_registered_tools
from app.types.retrieval import RetrievalControlsMap, RetrievalIntent, TimeBounds

FALLBACK_TOOL_NAMES: tuple[str, ...] = ("get_sre_guidance",)
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

    scored = _score_tools(state_any, available_tools)
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


def _score_tools(
    state: dict[str, Any],
    tools: list[RegisteredTool],
) -> list[PlannedInvestigationAction]:
    primary_sources = set(primary_sources_for_alert(state))
    candidate_sources = {str(tool.source) for tool in tools}
    relevant_sources = set(relevant_sources_for_alert(state, candidate_sources))
    alert_text = collect_alert_text(state)
    existing_evidence = state.get("evidence")
    evidence_keys = set(existing_evidence) if isinstance(existing_evidence, dict) else set()

    scored = [
        _score_tool(
            tool,
            alert_text=alert_text,
            primary_sources=primary_sources,
            relevant_sources=relevant_sources,
            evidence_keys=evidence_keys,
        )
        for tool in tools
    ]
    if scored and max(action.score for action in scored) <= 0:
        scored = [_score_fallback_tool(action) for action in scored]

    return sorted(
        scored, key=lambda item: (-item.score, item.source in SECONDARY_SOURCES, item.name)
    )


def _score_tool(
    tool: RegisteredTool,
    *,
    alert_text: str,
    primary_sources: set[str],
    relevant_sources: set[str],
    evidence_keys: set[str],
) -> PlannedInvestigationAction:
    source = str(tool.source)
    score = 0
    reasons: list[str] = []

    if source in primary_sources:
        score += 100
        reasons.append(f"source '{source}' matches alert source")
    if source in relevant_sources:
        score += 70
        reasons.append(f"source '{source}' matches alert context")
    if source in SECONDARY_SOURCES:
        score -= 10
        reasons.append("secondary source, used after integration-specific tools")

    metadata_text = " ".join(
        [
            tool.description,
            " ".join(tool.use_cases),
            " ".join(tool.examples),
            " ".join(tool.tags),
            str(tool.evidence_type or ""),
        ]
    ).lower()
    metadata_matches = _metadata_matches(alert_text, metadata_text)
    if metadata_matches:
        score += min(len(metadata_matches), 5) * 4
        reasons.append(f"metadata matched alert terms: {', '.join(metadata_matches[:5])}")

    if tool.name in evidence_keys:
        score -= 25
        reasons.append("tool already has evidence in state")

    if not reasons:
        reasons.append("no source or metadata match")

    return PlannedInvestigationAction(
        name=tool.name,
        source=source,
        score=score,
        reasons=tuple(reasons),
    )


def _metadata_matches(alert_text: str, metadata_text: str) -> list[str]:
    if not alert_text or not metadata_text:
        return []
    terms = {
        term.strip(".,:;()[]{}").lower()
        for term in alert_text.split()
        if len(term.strip(".,:;()[]{}")) >= 4
    }
    return sorted(term for term in terms if term in metadata_text)


def _score_fallback_tool(action: PlannedInvestigationAction) -> PlannedInvestigationAction:
    if action.name not in FALLBACK_TOOL_NAMES:
        return action
    return PlannedInvestigationAction(
        name=action.name,
        source=action.source,
        score=10,
        reasons=(*action.reasons, "included as deterministic fallback"),
    )


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
