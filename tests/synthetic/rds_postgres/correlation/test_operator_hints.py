from tests.synthetic.rds_postgres.correlation.operator_hints import score_operator_hints


def test_operator_hints_match_candidate_keywords() -> None:
    result = score_operator_hints(
        candidate_name="orders-web-asg",
        candidate_keywords=("web", "checkout", "automation"),
        operator_hints=("scheduled automation feature was recently introduced",),
    )

    assert result.score == 1.0
    assert result.matched_hints == ("scheduled automation feature was recently introduced",)


def test_operator_hints_score_low_without_match() -> None:
    result = score_operator_hints(
        candidate_name="orders-worker-asg",
        candidate_keywords=("worker",),
        operator_hints=("new checkout web flow deployed",),
    )

    assert result.score == 0.0
    assert result.matched_hints == ()
