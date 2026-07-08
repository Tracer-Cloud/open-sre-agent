"""Pure upstream-correlation scoring algorithms.

Scores time-window, topology, and periodicity signals for upstream candidates.
All functions are deterministic; output feeds upstream-correlation reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.domain.correlation.confidence import (
    EvidenceContribution,
    WeightedConfidence,
    build_weighted_confidence,
)
from core.domain.types.upstream import (
    HintEvidenceScore,
    PeriodicityScore,
    TimeSeries,
    TimeWindowCorrelation,
    TopologyCorrelation,
    TopologyNode,
    UpstreamCandidate,
)

# Evidence weights for candidate correlation (sum to 1.0).
TIME_WINDOW_WEIGHT = 0.45
TOPOLOGY_WEIGHT = 0.30
PERIODICITY_WEIGHT = 0.10
FEATURE_WORKFLOW_WEIGHT = 0.15


@dataclass(frozen=True)
class CandidateCorrelationScore:
    candidate_name: str
    time_window_score: float
    topology_score: float
    periodicity_score: float
    feature_workflow_score: float
    final_confidence: float
    weighted_confidence: WeightedConfidence
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
    operator_hint: HintEvidenceScore | None = None,
) -> CandidateCorrelationScore:
    periodicity_score = periodicity.score if periodicity is not None else 0.0
    feature_workflow_score = operator_hint.score if operator_hint is not None else 0.0
    feature_workflow_rationale = (
        operator_hint.rationale
        if operator_hint is not None
        else "No feature/workflow hint evidence."
    )

    weighted_confidence = build_weighted_confidence(
        (
            EvidenceContribution(
                source="correlation",
                score=time_window.score,
                weight=TIME_WINDOW_WEIGHT,
                rationale=time_window.rationale,
            ),
            EvidenceContribution(
                source="topology",
                score=topology.adjacency_score,
                weight=TOPOLOGY_WEIGHT,
                rationale=topology.rationale,
            ),
            EvidenceContribution(
                source="periodicity",
                score=periodicity_score,
                weight=PERIODICITY_WEIGHT,
                rationale=(
                    periodicity.rationale if periodicity is not None else "No periodicity evidence."
                ),
            ),
            EvidenceContribution(
                source="feature_workflow",
                score=feature_workflow_score,
                weight=FEATURE_WORKFLOW_WEIGHT,
                rationale=feature_workflow_rationale,
            ),
        )
    )

    return CandidateCorrelationScore(
        candidate_name=candidate_name,
        time_window_score=time_window.score,
        topology_score=topology.adjacency_score,
        periodicity_score=periodicity_score,
        feature_workflow_score=feature_workflow_score,
        final_confidence=weighted_confidence.score,
        weighted_confidence=weighted_confidence,
        rationale=(
            f"confidence={weighted_confidence.label}; "
            f"correlation={time_window.score}, "
            f"topology={topology.adjacency_score}, "
            f"periodicity={periodicity_score}, "
            f"feature_workflow={feature_workflow_score}"
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


__all__ = [
    "CandidateCorrelationScore",
    "FEATURE_WORKFLOW_WEIGHT",
    "PERIODICITY_WEIGHT",
    "TIME_WINDOW_WEIGHT",
    "TOPOLOGY_WEIGHT",
    "rank_upstream_candidates",
    "score_candidate_correlation",
    "score_periodic_spikes",
    "score_time_window_correlation",
    "score_topology_adjacency",
]
