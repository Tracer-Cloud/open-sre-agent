"""Typed planning records for investigation tool selection."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.domain.types.retrieval import RetrievalIntent


@dataclass(frozen=True)
class PlannedInvestigationAction:
    """One candidate investigation tool with its planning score and rationale."""

    name: str
    source: str
    score: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    retrieval_intent: RetrievalIntent | None = None


__all__ = ["PlannedInvestigationAction"]
