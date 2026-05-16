from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.correlation.models import UpstreamCandidate
from app.correlation.upstream import MetricSeries


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


@dataclass(frozen=True)
class CandidateCorrelationScore:
    candidate_name: str
    time_window_score: float
    topology_score: float
    periodicity_score: float
    operator_hint_score: float
    final_confidence: float
    rationale: str


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _trend(values: tuple[float, ...]) -> list[int]:
    trend: list[int] = []
    for previous, current in zip(values, values[1:], strict=False):
        if current > previous:
            trend.append(1)
        elif current < previous:
            trend.append(-1)
        else:
            trend.append(0)
    return trend


def _to_time_series(metric: MetricSeries) -> TimeSeries:
    return TimeSeries(
        name=metric.name,
        timestamps=metric.timestamps,
        values=metric.values,
    )


def score_time_window_correlation(
    primary: TimeSeries,
    candidate: TimeSeries,
) -> TimeWindowCorrelation:
    primary_points = {
        _parse_timestamp(timestamp): value
        for timestamp, value in zip(primary.timestamps, primary.values, strict=False)
    }
    candidate_points = {
        _parse_timestamp(timestamp): value
        for timestamp, value in zip(candidate.timestamps, candidate.values, strict=False)
    }

    common_timestamps = tuple(sorted(set(primary_points) & set(candidate_points)))
    if len(common_timestamps) < 2:
        return TimeWindowCorrelation(
            primary_signal=primary.name,
            candidate_signal=candidate.name,
            aligned_points=len(common_timestamps),
            direction_matches=0,
            score=0.0,
            rationale="Not enough overlapping timestamps to score time-window correlation.",
        )

    primary_values = tuple(primary_points[timestamp] for timestamp in common_timestamps)
    candidate_values = tuple(candidate_points[timestamp] for timestamp in common_timestamps)

    primary_trend = _trend(primary_values)
    candidate_trend = _trend(candidate_values)

    comparable_steps = [
        (primary_step, candidate_step)
        for primary_step, candidate_step in zip(primary_trend, candidate_trend, strict=False)
        if primary_step != 0 or candidate_step != 0
    ]

    if not comparable_steps:
        score = 0.0
        direction_matches = 0
    else:
        direction_matches = sum(
            1 for primary_step, candidate_step in comparable_steps if primary_step == candidate_step
        )
        score = round(direction_matches / len(comparable_steps), 4)

    return TimeWindowCorrelation(
        primary_signal=primary.name,
        candidate_signal=candidate.name,
        aligned_points=len(common_timestamps),
        direction_matches=direction_matches,
        score=score,
        rationale=(
            f"{candidate.name} matched {direction_matches}/{len(comparable_steps)} "
            f"time-window trend steps against {primary.name}."
        ),
    )


def score_topology_adjacency(
    *,
    source: TopologyNode,
    target: TopologyNode,
) -> TopologyCorrelation:
    if target.name in source.upstream_of:
        return TopologyCorrelation(
            source=source.name,
            target=target.name,
            adjacency_score=1.0,
            rationale=f"{source.name} is topology-adjacent to {target.name}.",
        )

    return TopologyCorrelation(
        source=source.name,
        target=target.name,
        adjacency_score=0.0,
        rationale=f"{source.name} is not topology-adjacent to {target.name}.",
    )


def score_periodic_spikes(
    *,
    signal_name: str,
    values: tuple[float, ...],
    spike_threshold: float,
) -> PeriodicityScore:
    repeated_spikes = sum(1 for value in values if value >= spike_threshold)

    if repeated_spikes <= 1:
        score = 0.0
        rationale = "No repeated spike pattern detected."
    else:
        score = 1.0
        rationale = f"Detected repeated threshold crossings for {signal_name}."

    return PeriodicityScore(
        signal_name=signal_name,
        repeated_spikes=repeated_spikes,
        score=round(score, 4),
        rationale=rationale,
    )


def score_candidate_correlation(
    *,
    candidate_name: str,
    time_window: TimeWindowCorrelation,
    topology: TopologyCorrelation,
    periodicity: PeriodicityScore | None = None,
    operator_hint: object | None = None,
) -> CandidateCorrelationScore:
    periodicity_score = periodicity.score if periodicity is not None else 0.0
    operator_hint_score = getattr(operator_hint, "score", 0.0) if operator_hint is not None else 0.0

    final_confidence = round(
        (
            time_window.score * 0.5
            + topology.adjacency_score * 0.3
            + periodicity_score * 0.1
            + operator_hint_score * 0.1
        ),
        4,
    )

    return CandidateCorrelationScore(
        candidate_name=candidate_name,
        time_window_score=time_window.score,
        topology_score=topology.adjacency_score,
        periodicity_score=periodicity_score,
        operator_hint_score=operator_hint_score,
        final_confidence=final_confidence,
        rationale=(
            f"time_window={time_window.score}, "
            f"topology={topology.adjacency_score}, "
            f"periodicity={periodicity_score}, "
            f"operator_hint={operator_hint_score}"
        ),
    )


def rank_upstream_candidates(
    candidates: list[UpstreamCandidate],
    *,
    top_n: int | None = None,
) -> list[UpstreamCandidate]:
    ranked = sorted(
        candidates,
        key=lambda candidate: (-candidate.confidence, candidate.name),
    )

    if top_n is None:
        return ranked
    if top_n <= 0:
        return []

    return ranked[:top_n]


def metric_to_time_series(metric: MetricSeries) -> TimeSeries:
    return _to_time_series(metric)
