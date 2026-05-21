"""Synthetic scenario resolution for deterministic action mapping."""

from __future__ import annotations

import re

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PromptClause,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
    DEFAULT_SYNTHETIC_SCENARIO,
    SYNTHETIC_UNKNOWN_PREFIX,
    list_rds_postgres_scenarios,
)

_SYNTHETIC_SCENARIO_ID_RE = re.compile(
    r"\b(?P<scenario>\d{3}-[a-z0-9][a-z0-9-]*)\b",
    re.IGNORECASE,
)
_SYNTHETIC_NUMERIC_HINT_RE = re.compile(r"\b(?P<num>\d{1,4})\b")
_SYNTHETIC_ALL_RE = re.compile(
    r"\b(?:all|entire)\b.{0,40}\b(?:synthetic|benchmark|tests?)\b"
    r"|"
    r"\b(?:synthetic|benchmark|tests?)\b.{0,40}\b(?:all|entire)\b"
    r"|"
    r"\bfull\s+(?:synthetic(?:\s+tests?)?|benchmark|suite)\b"
    r"|"
    r"\b(?:synthetic|benchmark|tests?)\b.{0,40}\bfull\s+suite\b",
    re.IGNORECASE,
)


def _resolve_numeric_hint(text: str, scenarios: tuple[str, ...]) -> tuple[str, int] | None:
    for match in _SYNTHETIC_NUMERIC_HINT_RE.finditer(text):
        raw = match.group("num")
        padded = raw.zfill(3) if len(raw) <= 3 else raw
        matched = [name for name in scenarios if name.startswith(f"{padded}-")]
        if matched:
            return matched[0], match.start()
    return None


def _detect_unresolved_numeric_hint(text: str, scenarios: tuple[str, ...]) -> str | None:
    for match in _SYNTHETIC_NUMERIC_HINT_RE.finditer(text):
        raw = match.group("num")
        padded = raw.zfill(3) if len(raw) <= 3 else raw
        if not any(name.startswith(f"{padded}-") for name in scenarios):
            return raw
    return None


def resolve_synthetic_action_content(
    clause: PromptClause, *, synthetic_start: int
) -> tuple[str, int]:
    """Resolve scenario content and action position for a synthetic-test clause."""
    if _SYNTHETIC_ALL_RE.search(clause.text) is not None:
        return (
            "rds_postgres:all",
            clause.position + synthetic_start,
        )

    full_match = _SYNTHETIC_SCENARIO_ID_RE.search(clause.text)
    if full_match is not None:
        scenario_id = full_match.group("scenario").lower()
        return (
            f"rds_postgres:{scenario_id}",
            clause.position + full_match.start("scenario"),
        )

    scenarios = list_rds_postgres_scenarios()
    resolved = _resolve_numeric_hint(clause.text, scenarios)
    if resolved is not None:
        scenario_id, match_start = resolved
        return (
            f"rds_postgres:{scenario_id}",
            clause.position + match_start,
        )

    unresolved_hint = _detect_unresolved_numeric_hint(clause.text, scenarios)
    if unresolved_hint is not None:
        return (
            f"{SYNTHETIC_UNKNOWN_PREFIX}{unresolved_hint}",
            clause.position + synthetic_start,
        )

    return (
        f"rds_postgres:{DEFAULT_SYNTHETIC_SCENARIO}",
        clause.position + synthetic_start,
    )


__all__ = ["resolve_synthetic_action_content"]
