"""Formatting helpers for streamed investigation output."""

from __future__ import annotations

import math
import re
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


def _clean_markdown_line(line: str) -> str:
    """Strip both bulleted lists (•, ●, -, —, *) and numbered lists (e.g. 1., 2))."""
    stripped = line.strip()
    prev = ""
    while stripped != prev:
        prev = stripped
        stripped = re.sub(r"^[-•●—]\s+", "", stripped)
        # Markdown ``* item`` list marker only — not ``*Italic Section:*`` headings.
        stripped = re.sub(r"^\*\s+", "", stripped)
        stripped = re.sub(r"^\d+[.)]\s+", "", stripped)
    return stripped


def _normalized_report_heading_inner(line: str) -> str:
    """Normalize LLM report lines for heading keyword matching."""
    s = line.strip()
    while s.startswith("#"):
        s = s[1:].strip()
    if s.startswith("**"):
        core = s[2:]
        if core.endswith("**:"):
            core = core[:-3]
        elif core.endswith("**"):
            core = core[:-2]
        return core.strip()
    if len(s) >= 2 and s.startswith("[") and s.endswith("]") and ":" not in s:
        return s[1:-1].strip()
    if (
        len(s) >= 3
        and s.startswith("*")
        and s.endswith("*")
        and not s.startswith("* ")
        and "**" not in s
    ):
        inner = s[1:-1].strip()
        if ":" in inner or len(inner.split()) >= 3:
            return inner
    return s.strip()


def _report_line_looks_like_heading(line: str, *, inner: str) -> bool:
    """True if the line uses a heading-like structure (not prose)."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    is_bracket = (
        stripped.startswith("[") and stripped.rstrip().endswith("]") and ":" not in stripped
    )
    is_bold_md = stripped.startswith("**") and (stripped.endswith("**") or stripped.endswith("**:"))
    wrapped_ast = (
        len(stripped) >= 3
        and stripped.startswith("*")
        and stripped.endswith("*")
        and not stripped.startswith("* ")
        and "**" not in stripped
        and (":" in stripped[1:-1] or len(stripped[1:-1].strip().split()) >= 3)
    )
    shouty = inner.isupper() and len(inner.replace(" ", "")) >= 8 and len(inner.split()) <= 14
    return bool(is_bracket or is_bold_md or wrapped_ast or shouty)


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
