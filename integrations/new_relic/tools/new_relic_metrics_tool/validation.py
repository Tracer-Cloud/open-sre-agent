"""Sanity-check and default-window/limit injection for model-supplied NRQL.

The NRQL text comes straight from the LLM (FR-7), so this module is the one
place that decides whether it is safe to run and fills in the defaults the
model omitted. NRQL has no DML/DDL of its own — every write happens through a
separate GraphQL ``mutation``, never through ``nrql(query: ...)`` — so the
keyword check below is defensive only, guarding against a malformed request
(e.g. a GraphQL mutation string passed in as "nrql") rather than a real NRQL
write capability.
"""

from __future__ import annotations

import re

from config.constants.new_relic import (
    NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
    NEW_RELIC_DEFAULT_WINDOW_MINUTES,
    NEW_RELIC_NRQL_LIMIT_MAX,
)

_SINCE_PATTERN = re.compile(r"\bSINCE\b", re.IGNORECASE)
_LIMIT_PATTERN = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)

#: Defensive-only keywords: NRQL has no mutation syntax, so any of these
#: appearing in a supposed NRQL string signals a malformed/smuggled request.
_FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "mutation",
    "delete ",
    "drop ",
    "insert ",
    "update ",
    "alter ",
)


def validate_nrql(nrql: str) -> tuple[bool, str]:
    """Return ``(is_valid, error)`` for a raw NRQL string received from the model."""
    text = str(nrql or "").strip()
    if not text:
        return False, "nrql query cannot be empty."
    lowered = text.lower()
    if not lowered.startswith("select"):
        return False, "nrql query must be a read-only SELECT statement."
    for keyword in _FORBIDDEN_KEYWORDS:
        if keyword in lowered:
            return False, f"nrql query must not contain '{keyword.strip()}'."
    return True, ""


def apply_default_window_and_limit(
    nrql: str,
    *,
    since_minutes: int = NEW_RELIC_DEFAULT_WINDOW_MINUTES,
    limit: int = NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
) -> str:
    """Inject a default ``SINCE``/``LIMIT`` when the model's NRQL omits them.

    The 5s NRQL-via-API timeout (NFR-7) makes an unbounded query a near
    guaranteed failure on a large account, so both clauses are added rather
    than left to the model's discretion. An explicit ``LIMIT`` above the
    vendor's own ceiling is clamped down to it, never raised.
    """
    text = nrql.strip()
    if not _SINCE_PATTERN.search(text):
        text = f"{text} SINCE {int(since_minutes)} minutes ago"

    limit_match = _LIMIT_PATTERN.search(text)
    if limit_match is None:
        text = f"{text} LIMIT {int(limit)}"
    elif int(limit_match.group(1)) > NEW_RELIC_NRQL_LIMIT_MAX:
        text = _LIMIT_PATTERN.sub(f"LIMIT {NEW_RELIC_NRQL_LIMIT_MAX}", text, count=1)

    return text


def extract_limit(nrql: str) -> int | None:
    """Return the numeric ``LIMIT`` clause in *nrql*, or ``None`` if absent."""
    match = _LIMIT_PATTERN.search(nrql)
    return None if match is None else int(match.group(1))
