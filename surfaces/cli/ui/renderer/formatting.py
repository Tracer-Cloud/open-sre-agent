"""Formatting helpers for streamed investigation output."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from config.constants.investigation import MAX_INVESTIGATION_LOOPS


def format_prior_tools_clause(
    tools: Sequence[str],
    *,
    max_tools: int = 3,
) -> str:
    """Appendix naming tools gathered since the previous LLM lap."""
    if not tools:
        return ""
    labels: list[str] = []
    counts: dict[str, int] = {}
    for label in tools:
        stripped = label.strip()
        if not stripped:
            continue
        counts[stripped] = counts.get(stripped, 0) + 1
        if stripped not in labels:
            labels.append(stripped)
    if not labels:
        return ""
    rendered = [
        f"{label} x{counts[label]}" if counts[label] > 1 else label for label in labels[:max_tools]
    ]
    suffix = ", ..." if len(labels) > max_tools else ""
    return f" after {', '.join(rendered)}{suffix}"


def investigation_llm_progress_hint(
    iteration: int,
    *,
    max_loops: int = MAX_INVESTIGATION_LOOPS,
    prior_tools: Sequence[str] | None = None,
) -> str:
    """Human-readable status for one investigation-agent LLM lap.

    Each ``llm_start`` event maps to one ReAct think step: the model reads
    accumulated alert + tool evidence and either requests more tools or stops.
    """
    lap = iteration + 1
    cap = f"lap {lap}/{max_loops}"
    tools_clause = format_prior_tools_clause(prior_tools or ())
    if iteration == 0:
        return f"Planning investigation ({cap}){tools_clause}"
    return f"Reviewing evidence ({cap}){tools_clause}"


def _validity_score_percent(score: Any) -> str | None:
    """Format a 0..1 validity score for display, or None if the payload is unusable."""
    if score is None or isinstance(score, bool):
        return None
    if not isinstance(score, (int, float)):
        return None
    v = float(score)
    if not math.isfinite(v):
        return None
    v = max(0.0, min(1.0, v))
    return f"{int(v * 100)}%"
