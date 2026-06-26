from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceContribution:
    source: str
    score: float
    weight: float
    rationale: str


@dataclass(frozen=True)
class SharedConfidence:
    score: float
    label: str
    contributions: tuple[EvidenceContribution, ...]


def _label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def build_shared_confidence(
    contributions: tuple[EvidenceContribution, ...],
) -> SharedConfidence:
    total_weight = sum(item.weight for item in contributions)
    if total_weight <= 0:
        score = 0.0
    else:
        score = sum(item.score * item.weight for item in contributions) / total_weight

    rounded = round(score, 4)
    return SharedConfidence(
        score=rounded,
        label=_label(rounded),
        contributions=contributions,
    )
