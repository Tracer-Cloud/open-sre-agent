from tests.synthetic.rds_postgres.correlation.periodicity import score_periodic_spikes


def test_periodic_spikes_score_repeated_threshold_crossings() -> None:
    result = score_periodic_spikes(
        signal_name="RDS CPU",
        values=(20.0, 82.0, 30.0, 85.0, 28.0, 88.0),
        spike_threshold=80.0,
    )

    assert result.repeated_spikes == 3
    assert result.score == 1.0


def test_periodic_spikes_score_low_when_not_repeated() -> None:
    result = score_periodic_spikes(
        signal_name="RDS CPU",
        values=(20.0, 82.0, 30.0, 35.0),
        spike_threshold=80.0,
    )

    assert result.repeated_spikes == 1
    assert result.score == 0.0
