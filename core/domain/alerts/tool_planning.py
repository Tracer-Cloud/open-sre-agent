"""Pure scoring rules for alert-driven investigation tool planning."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from core.domain.alerts.alert_source import (
    collect_alert_text,
    primary_sources_for_alert,
    relevant_sources_for_alert,
    secondary_tool_sources,
)
from core.domain.types.planning import PlannedInvestigationAction
from core.tool_framework.tags import FALLBACK_PLANNING_TAG


class PlannableTool(Protocol):
    """Read-only view of the tool fields the alert planner scores against."""

    @property
    def name(self) -> str:
        """Stable tool name used in plans and evidence keys."""

    @property
    def source(self) -> str:
        """Integration / capability source the tool belongs to."""

    @property
    def description(self) -> str:
        """Short description scored against alert text."""

    @property
    def use_cases(self) -> Sequence[str]:
        """When this tool should be considered."""

    @property
    def examples(self) -> Sequence[str]:
        """Example prompts or payloads for metadata matching."""

    @property
    def tags(self) -> Sequence[str]:
        """Planning tags (e.g. fallback)."""

    @property
    def evidence_type(self) -> Any:
        """Evidence kind this tool produces, if any."""


def score_tools(
    state: dict[str, Any],
    tools: Sequence[PlannableTool],
) -> list[PlannedInvestigationAction]:
    primary_sources = set(primary_sources_for_alert(state))
    candidate_sources = {str(tool.source) for tool in tools}
    relevant_sources = set(relevant_sources_for_alert(state, candidate_sources))
    alert_text = collect_alert_text(state)
    existing_evidence = state.get("evidence")
    evidence_keys = set(existing_evidence) if isinstance(existing_evidence, dict) else set()
    secondary_sources = secondary_tool_sources()

    scored = [
        score_tool(
            tool,
            alert_text=alert_text,
            primary_sources=primary_sources,
            relevant_sources=relevant_sources,
            evidence_keys=evidence_keys,
            secondary_sources=secondary_sources,
        )
        for tool in tools
    ]
    if scored and max(action.score for action in scored) <= 0:
        scored = [score_fallback_tool(action) for action in scored]

    return sorted(
        scored, key=lambda item: (-item.score, item.source in secondary_sources, item.name)
    )


def score_tool(
    tool: PlannableTool,
    *,
    alert_text: str,
    primary_sources: set[str],
    relevant_sources: set[str],
    evidence_keys: set[str],
    secondary_sources: frozenset[str] | set[str] | None = None,
) -> PlannedInvestigationAction:
    source = str(tool.source)
    score = 0
    reasons: list[str] = []
    secondary = secondary_sources if secondary_sources is not None else secondary_tool_sources()

    if source in primary_sources:
        score += 100
        reasons.append(f"source '{source}' matches alert source")
    if source in relevant_sources:
        score += 70
        reasons.append(f"source '{source}' matches alert context")
    if source in secondary:
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
        is_fallback=FALLBACK_PLANNING_TAG in tool.tags,
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
    if not action.is_fallback:
        return action
    return PlannedInvestigationAction(
        name=action.name,
        source=action.source,
        score=10,
        reasons=(*action.reasons, "included as deterministic fallback"),
        is_fallback=True,
    )


__all__ = [
    "PlannableTool",
    "metadata_matches_for_alert",
    "score_fallback_tool",
    "score_tool",
    "score_tools",
]
