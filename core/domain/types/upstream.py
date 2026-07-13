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
    confidence_label: str = "low"
    evidence_breakdown: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class MetricSeries:
    source: str
    name: str
    timestamps: tuple[str, ...]
    values: tuple[float, ...]


@dataclass(frozen=True)
class TimeSeries:
    name: str
    timestamps: tuple[str, ...]
    values: tuple[float, ...]


@dataclass(frozen=True)
class TimeWindowCorrelation:
    primary_signal: str
    candidate_signal: str
    aligned_points: int
    direction_matches: int
    score: float
    rationale: str


@dataclass(frozen=True)
class TopologyNode:
    name: str
    node_type: str
    upstream_of: tuple[str, ...]


@dataclass(frozen=True)
class TopologyCorrelation:
    source: str
    target: str
    adjacency_score: float
    rationale: str


@dataclass(frozen=True)
class PeriodicityScore:
    signal_name: str
    repeated_spikes: int
    score: float
    rationale: str


class HintEvidenceScore(Protocol):
    @property
    def score(self) -> float:
        raise NotImplementedError

    @property
    def rationale(self) -> str:
        raise NotImplementedError


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


def metric_to_time_series(metric: MetricSeries) -> TimeSeries:
    return TimeSeries(
        name=metric.name,
        timestamps=metric.timestamps,
        values=metric.values,
    )


__all__ = [
    "CorrelatedSignal",
    "HintEvidenceScore",
    "LogSignal",
    "MetricSeries",
    "PeriodicityScore",
    "TimeSeries",
    "TimeWindowCorrelation",
    "TopologyCorrelation",
    "TopologyHint",
    "TopologyNode",
    "UpstreamCandidate",
    "UpstreamEvidenceBundle",
    "UpstreamEvidenceProvider",
    "metric_to_time_series",
]
