from tests.synthetic.rds_postgres.correlation.time_window import (
    TimeSeries,
    score_time_window_correlation,
)


def test_time_window_correlation_scores_aligned_rising_signals() -> None:
    primary = TimeSeries(
        name="RDS CPUUtilization",
        timestamps=(
            "2026-04-15T14:00:00Z",
            "2026-04-15T14:01:00Z",
            "2026-04-15T14:02:00Z",
            "2026-04-15T14:03:00Z",
        ),
        values=(22.4, 24.1, 28.6, 33.9),
    )
    candidate = TimeSeries(
        name="EC2 web tier CPU",
        timestamps=primary.timestamps,
        values=(30.1, 35.0, 46.0, 55.2),
    )

    result = score_time_window_correlation(primary, candidate)

    assert result.aligned_points == 4
    assert result.direction_matches == 3
    assert result.score == 1.0
    assert result.primary_signal == "RDS CPUUtilization"
    assert result.candidate_signal == "EC2 web tier CPU"


def test_time_window_correlation_scores_flat_candidate_low() -> None:
    primary = TimeSeries(
        name="RDS DatabaseConnections",
        timestamps=(
            "2026-04-15T14:00:00Z",
            "2026-04-15T14:01:00Z",
            "2026-04-15T14:02:00Z",
        ),
        values=(120, 130, 162),
    )
    candidate = TimeSeries(
        name="EC2 worker tier CPU",
        timestamps=primary.timestamps,
        values=(25.0, 25.0, 25.0),
    )

    result = score_time_window_correlation(primary, candidate)

    assert result.aligned_points == 3
    assert result.direction_matches == 0
    assert result.score == 0.0
