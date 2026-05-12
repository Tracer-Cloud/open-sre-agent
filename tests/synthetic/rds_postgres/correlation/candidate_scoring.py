from __future__ import annotations

from dataclasses import dataclass

from tests.synthetic.rds_postgres.correlation.operator_hints import OperatorHintScore
from tests.synthetic.rds_postgres.correlation.periodicity import PeriodicityScore
from tests.synthetic.rds_postgres.correlation.time_window import TimeWindowCorrelation
from tests.synthetic.rds_postgres.correlation.topology import TopologyCorrelation


@dataclass(frozen=True)
class CandidateCorrelationScore:
    candidate_name: str
    time_window_score: float
    topology_score: float
    periodicity_score: float
    operator_hint_score: float
    final_confidence: float
    rationale: str


def score_candidate_correlation(
    *,
    candidate_name: str,
    time_window: TimeWindowCorrelation,
    topology: TopologyCorrelation,
    periodicity: PeriodicityScore | None = None,
    operator_hint: OperatorHintScore | None = None,
) -> CandidateCorrelationScore:
    periodicity_score = periodicity.score if periodicity is not None else 0.0
    operator_hint_score = operator_hint.score if operator_hint is not None else 0.0

    final_confidence = round(
        (
            time_window.score * 0.5
            + topology.adjacency_score * 0.3
            + periodicity_score * 0.1
            + operator_hint_score * 0.1
        ),
        4,
    )

    rationale = (
        f"{candidate_name} scored "
        f"time_window={time_window.score:.2f}, "
        f"topology={topology.adjacency_score:.2f}, "
        f"periodicity={periodicity_score:.2f}, "
        f"operator_hint={operator_hint_score:.2f}, "
        f"final={final_confidence:.2f}."
    )

    return CandidateCorrelationScore(
        candidate_name=candidate_name,
        time_window_score=time_window.score,
        topology_score=topology.adjacency_score,
        periodicity_score=periodicity_score,
        operator_hint_score=operator_hint_score,
        final_confidence=final_confidence,
        rationale=rationale,
    )
