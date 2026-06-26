"""Domain entities for upstream-correlation evidence and results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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


@dataclass(frozen=True)
class MetricSeries:
    source: str
    name: str
    timestamps: tuple[str, ...]
    values: tuple[float, ...]


@dataclass(frozen=True)
class LogSignal:
    source: str
    name: str
    timestamps: tuple[str, ...]
    messages: tuple[str, ...]


@dataclass(frozen=True)
class TopologyHint:
    source: str
    target: str
    relation: str


@dataclass(frozen=True)
class UpstreamEvidenceBundle:
    rds_metrics: tuple[MetricSeries, ...] = ()
    upstream_metrics: tuple[MetricSeries, ...] = ()
    web_request_logs: tuple[LogSignal, ...] = ()
    app_logs: tuple[LogSignal, ...] = ()
    topology_hints: tuple[TopologyHint, ...] = ()
    operator_hints: tuple[str, ...] = ()


class UpstreamEvidenceProvider(Protocol):
    def collect_upstream_evidence(
        self,
        *,
        alert_id: str,
        service_name: str,
        window_start: str,
        window_end: str,
    ) -> UpstreamEvidenceBundle:
        """Collect evidence needed for symptom-first upstream correlation."""


__all__ = [
    "CorrelatedSignal",
    "LogSignal",
    "MetricSeries",
    "TopologyHint",
    "UpstreamCandidate",
    "UpstreamEvidenceBundle",
    "UpstreamEvidenceProvider",
]
