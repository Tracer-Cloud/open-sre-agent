"""Alert-context selectors shared by investigation planning."""

from __future__ import annotations

from typing import Any

from app.agent.utils.alert_source import (
    ALERT_SOURCE_TO_TOOL_SOURCES,
    SECONDARY_TOOL_SOURCES,
    SOURCE_ALIASES,
    resolve_alert_source,
)

SECONDARY_SOURCES = SECONDARY_TOOL_SOURCES


def primary_sources_for_alert(state: dict[str, Any]) -> tuple[str, ...]:
    """Return source keys that directly match the parsed alert source."""
    return ALERT_SOURCE_TO_TOOL_SOURCES.get(resolve_alert_source(state), ())


def relevant_sources_for_alert(
    state: dict[str, Any],
    candidate_sources: set[str],
) -> list[str]:
    """Select candidate sources relevant to the alert content."""
    candidates = sorted(source for source in candidate_sources if source not in SECONDARY_SOURCES)
    if not candidates:
        return []

    declared = declared_context_sources(state)
    if declared:
        from_declared = [source for source in candidates if source in declared]
        if from_declared:
            return from_declared

    text = collect_alert_text(state)
    if not text:
        return []

    matched: list[str] = []
    for source in candidates:
        keywords = {source, *SOURCE_ALIASES.get(source, ())}
        if any(keyword in text for keyword in keywords):
            matched.append(source)
    return matched


def declared_context_sources(state: dict[str, Any]) -> set[str]:
    """Return explicit context source annotations from the raw alert, if any."""
    raw = state.get("raw_alert")
    if not isinstance(raw, dict):
        return set()
    for block_key in ("commonAnnotations", "annotations", "commonLabels", "labels"):
        block = raw.get(block_key)
        if isinstance(block, dict):
            value = block.get("context_sources")
            if isinstance(value, str) and value.strip():
                return {item.strip().lower() for item in value.split(",") if item.strip()}
    return set()


def collect_alert_text(state: dict[str, Any]) -> str:
    """Collect searchable alert text for deterministic source/tool matching."""
    parts: list[str] = [
        str(state.get("alert_name") or ""),
        str(state.get("pipeline_name") or ""),
        str(state.get("message") or ""),
    ]
    raw = state.get("raw_alert")
    if isinstance(raw, dict):
        for key in ("alert_name", "title", "message", "text", "error_message", "kube_namespace"):
            value = raw.get(key)
            if isinstance(value, str):
                parts.append(value)
        for block_key in ("commonAnnotations", "annotations", "commonLabels", "labels"):
            block = raw.get(block_key)
            if isinstance(block, dict):
                parts.extend(str(v) for v in block.values() if isinstance(v, (str, int, float)))
    elif isinstance(raw, str):
        parts.append(raw)

    problem_md = state.get("problem_md")
    if isinstance(problem_md, str):
        parts.append(problem_md)

    return " ".join(part for part in parts if part).lower()


__all__ = [
    "ALERT_SOURCE_TO_TOOL_SOURCES",
    "SECONDARY_SOURCES",
    "SOURCE_ALIASES",
    "collect_alert_text",
    "declared_context_sources",
    "primary_sources_for_alert",
    "relevant_sources_for_alert",
]
