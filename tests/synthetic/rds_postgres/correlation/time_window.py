from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
