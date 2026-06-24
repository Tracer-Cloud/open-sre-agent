from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CorrelatedSignal:
    source: str
    name: str
    description: str
    score: float


@dataclass(frozen=True)
class UpstreamCandidate:
    name: str
    tier: str
    confidence: float
    correlated_signals: tuple[CorrelatedSignal, ...]
    rationale: str
