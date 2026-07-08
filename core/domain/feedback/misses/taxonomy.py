"""Miss taxonomy enum and record shape."""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict


class MissTaxonomy(StrEnum):
    """Top-level failure modes for an inaccurate investigation outcome.

    The four buckets map to the four levers we have to improve accuracy:
    data we fetch, how we reason over it, the tools that fetch it, and how the
    router/prompt frames the problem.
    """

    RETRIEVAL_GAP = "retrieval_gap"
    REASONING_GAP = "reasoning_gap"
    TOOL_FAILURE = "tool_failure"
    ROUTING_FAILURE = "routing_failure"
    UNKNOWN = "unknown"


# (key, human label) — used by the CLI picker and the docs alike.
_TAXONOMY_LABELS: list[tuple[MissTaxonomy, str]] = [
    (MissTaxonomy.RETRIEVAL_GAP, "Retrieval gap — missing/insufficient evidence"),
    (MissTaxonomy.REASONING_GAP, "Reasoning gap — had the evidence, wrong conclusion"),
    (MissTaxonomy.TOOL_FAILURE, "Tool failure — a tool errored or returned bad data"),
    (MissTaxonomy.ROUTING_FAILURE, "Routing/prompt failure — wrong tools/plan/prompt"),
    (MissTaxonomy.UNKNOWN, "Unknown / not sure"),
]


def taxonomy_choices() -> list[tuple[str, str]]:
    """Return ``(key, label)`` pairs in the order the picker should show them."""
    return [(t.value, label) for t, label in _TAXONOMY_LABELS]


class MissRecord(TypedDict, total=False):
    """One row in ``misses.jsonl``.

    All fields are JSON-serialisable. Optional fields may be absent on
    older records and consumers must treat the schema as additive only.
    """

    miss_id: str
    feedback_id: str
    timestamp: str
    run_id: str
    alert_name: str
    pipeline_name: str
    severity: str
    rating: str
    taxonomy: str
    taxonomy_detail: str
    root_cause: str
    root_cause_category: str
    validity_score: float | None
    investigation_loop_count: int | None
    user_id: str
    org_id: str


__all__ = [
    "MissRecord",
    "MissTaxonomy",
    "taxonomy_choices",
]
