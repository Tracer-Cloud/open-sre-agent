"""Pure scoring rules for alert-driven investigation tool planning."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.domain.alerts.alert_source import (
    SECONDARY_TOOL_SOURCES as SECONDARY_SOURCES,
)
from app.core.domain.alerts.alert_source import (
    collect_alert_text,
    primary_sources_for_alert,
    relevant_sources_for_alert,
)
from app.core.domain.types.planning import PlannedInvestigationAction

FALLBACK_TOOL_NAMES: tuple[str, ...] = ("get_sre_guidance",)


def score_tools(
    state: dict[str, Any],
    tools: Sequence[Any],
) -> list[PlannedInvestigationAction]:
    primary_sources = set(primary_sources_for_alert(state))
    candidate_sources = {str(tool.source) for tool in tools}
    relevant_sources = set(relevant_sources_for_alert(state, candidate_sources))
    alert_text = collect_alert_text(state)
    existing_evidence = state.get("evidence")
    evidence_keys = set(existing_evidence) if isinstance(existing_evidence, dict) else set()

    scored = [
        score_tool(
            tool,
            alert_text=alert_text,
            primary_sources=primary_sources,
            relevant_sources=relevant_sources,
            evidence_keys=evidence_keys,
        )
        for tool in tools
    ]
    if scored and max(action.score for action in scored) <= 0:
        scored = [score_fallback_tool(action) for action in scored]

    return sorted(
        scored, key=lambda item: (-item.score, item.source in SECONDARY_SOURCES, item.name)
    )


def score_tool(
    tool: Any,
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
    metadata_matches = metadata_matches_for_alert(alert_text, metadata_text)
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


def metadata_matches_for_alert(alert_text: str, metadata_text: str) -> list[str]:
    if not alert_text or not metadata_text:
        return []
    terms = {
        term.strip(".,:;()[]{}").lower()
        for term in alert_text.split()
        if len(term.strip(".,:;()[]{}")) >= 4
    }
    return sorted(term for term in terms if term in metadata_text)


def score_fallback_tool(
    action: PlannedInvestigationAction,
) -> PlannedInvestigationAction:
    if action.name not in FALLBACK_TOOL_NAMES:
        return action
    return PlannedInvestigationAction(
        name=action.name,
        source=action.source,
        score=10,
        reasons=(*action.reasons, "included as deterministic fallback"),
    )


__all__ = [
    "FALLBACK_TOOL_NAMES",
    "metadata_matches_for_alert",
    "score_fallback_tool",
    "score_tool",
    "score_tools",
]
