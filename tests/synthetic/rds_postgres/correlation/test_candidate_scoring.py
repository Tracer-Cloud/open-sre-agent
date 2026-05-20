from tests.synthetic.rds_postgres.correlation.candidate_scoring import (
    score_candidate_correlation,
)
from tests.synthetic.rds_postgres.correlation.operator_hints import OperatorHintScore
from tests.synthetic.rds_postgres.correlation.periodicity import PeriodicityScore
from tests.synthetic.rds_postgres.correlation.time_window import TimeWindowCorrelation
from tests.synthetic.rds_postgres.correlation.topology import TopologyCorrelation


def test_candidate_correlation_combines_time_window_and_topology() -> None:
    time_window = TimeWindowCorrelation(
        primary_signal="RDS CPU",
        candidate_signal="web tier CPU",
        aligned_points=10,
        direction_matches=9,
        score=0.9,
        rationale="Strong alignment.",
    )

    topology = TopologyCorrelation(
        source="orders-web-asg",
        target="orders-prod-mysql",
        adjacency_score=1.0,
        rationale="Direct upstream dependency.",
    )

    result = score_candidate_correlation(
        candidate_name="orders-web-asg",
        time_window=time_window,
        topology=topology,
    )

    assert result.candidate_name == "orders-web-asg"
    assert result.time_window_score == 0.9
    assert result.topology_score == 1.0
    assert result.periodicity_score == 0.0
    assert result.operator_hint_score == 0.0
    assert result.final_confidence == 0.75


def test_candidate_correlation_penalizes_unrelated_candidate() -> None:
    time_window = TimeWindowCorrelation(
        primary_signal="RDS CPU",
        candidate_signal="worker tier CPU",
        aligned_points=10,
        direction_matches=1,
        score=0.1,
        rationale="Weak alignment.",
    )

    topology = TopologyCorrelation(
        source="orders-worker-asg",
        target="orders-prod-mysql",
        adjacency_score=0.0,
        rationale="No topology adjacency.",
    )

    result = score_candidate_correlation(
        candidate_name="orders-worker-asg",
        time_window=time_window,
        topology=topology,
    )

    assert result.final_confidence == 0.05


def test_candidate_correlation_includes_periodicity_and_operator_hint() -> None:
    time_window = TimeWindowCorrelation(
        primary_signal="RDS CPU",
        candidate_signal="web tier CPU",
        aligned_points=10,
        direction_matches=9,
        score=0.9,
        rationale="Strong alignment.",
    )
    topology = TopologyCorrelation(
        source="orders-web-asg",
        target="orders-prod-mysql",
        adjacency_score=1.0,
        rationale="Direct upstream dependency.",
    )
    periodicity = PeriodicityScore(
        signal_name="web tier CPU",
        repeated_spikes=3,
        score=1.0,
        rationale="Repeated spikes detected.",
    )
    operator_hint = OperatorHintScore(
        candidate_name="orders-web-asg",
        matched_hints=("scheduled automation feature was recently introduced",),
        score=1.0,
        rationale="Matched operator hint.",
    )

    result = score_candidate_correlation(
        candidate_name="orders-web-asg",
        time_window=time_window,
        topology=topology,
        periodicity=periodicity,
        operator_hint=operator_hint,
    )

    assert result.periodicity_score == 1.0
    assert result.operator_hint_score == 1.0
    assert result.final_confidence == 0.95
