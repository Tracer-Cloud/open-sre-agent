"""Weighted confidence from scored evidence contributions.

An ``EvidenceContribution`` is one scored signal (correlation, topology, etc.)
with an explicit weight. ``build_weighted_confidence`` returns the weighted
average and a high/medium/low label consumed by upstream-correlation reporting.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "EvidenceContribution",
    "WeightedConfidence",
    "build_weighted_confidence",
]


@dataclass(frozen=True)
class EvidenceContribution:
    source: str
    score: float
    weight: float
    rationale: str


@dataclass(frozen=True)
class WeightedConfidence:
    score: float
    label: str
    contributions: tuple[EvidenceContribution, ...]


def _label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def build_weighted_confidence(
    contributions: tuple[EvidenceContribution, ...],
) -> WeightedConfidence:
    total_weight = sum(item.weight for item in contributions)
    if total_weight <= 0:
        score = 0.0
    else:
        score = sum(item.score * item.weight for item in contributions) / total_weight

    rounded = round(score, 4)
    return WeightedConfidence(
        score=rounded,
        label=_label(rounded),
        contributions=contributions,
    )
