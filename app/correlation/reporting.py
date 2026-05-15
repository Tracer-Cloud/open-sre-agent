from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.correlation.models import CorrelatedSignal, UpstreamCandidate


@dataclass(frozen=True)
class CorrelationReport:
    correlated_signals: tuple[CorrelatedSignal, ...]
    most_likely_causal_drivers: tuple[UpstreamCandidate, ...]


def build_correlation_report(
    *,
    correlated_signals: tuple[CorrelatedSignal, ...],
    ranked_candidates: list[UpstreamCandidate],
    top_n: int = 3,
) -> CorrelationReport:
    if top_n <= 0:
        drivers = tuple(ranked_candidates)
    else:
        drivers = tuple(ranked_candidates[:top_n])

    return CorrelationReport(
        correlated_signals=correlated_signals,
        most_likely_causal_drivers=drivers,
    )


def correlation_report_to_payload(report: CorrelationReport) -> dict[str, Any]:
    return {
        "correlated_signals": [asdict(signal) for signal in report.correlated_signals],
        "most_likely_causal_drivers": [
            asdict(candidate) for candidate in report.most_likely_causal_drivers
        ],
    }
