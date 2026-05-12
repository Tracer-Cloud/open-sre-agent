from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PeriodicityScore:
    signal_name: str
    repeated_spikes: int
    score: float
    rationale: str


def score_periodic_spikes(
    *,
    signal_name: str,
    values: tuple[float, ...],
    spike_threshold: float,
    min_repeated_spikes: int = 2,
) -> PeriodicityScore:
    spike_count = sum(1 for value in values if value >= spike_threshold)
    score = 1.0 if spike_count >= min_repeated_spikes else 0.0

    return PeriodicityScore(
        signal_name=signal_name,
        repeated_spikes=spike_count,
        score=score,
        rationale=(f"{signal_name} had {spike_count} points above threshold {spike_threshold}."),
    )
