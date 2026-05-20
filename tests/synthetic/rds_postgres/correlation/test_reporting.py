from tests.synthetic.rds_postgres.correlation.models import (
    CorrelatedSignal,
    UpstreamCandidate,
)
from tests.synthetic.rds_postgres.correlation.reporting import (
    build_correlation_report,
    correlation_report_to_payload,
)


def test_correlation_report_separates_signals_from_causal_drivers() -> None:
    signal = CorrelatedSignal(
        source="aws_cloudwatch_metrics",
        name="EC2WebTierCPU",
        description="Web tier CPU rose with RDS CPU.",
        score=1.0,
    )
    candidate = UpstreamCandidate(
        name="orders-web-asg",
        tier="web",
        confidence=0.95,
        correlated_signals=(signal,),
        rationale="Web tier is time-aligned and topology-adjacent to RDS.",
    )

    report = build_correlation_report(
        correlated_signals=(signal,),
        ranked_candidates=[candidate],
    )
    payload = correlation_report_to_payload(report)

    assert list(payload) == ["correlated_signals", "most_likely_causal_drivers"]
    assert payload["correlated_signals"][0]["name"] == "EC2WebTierCPU"
    assert payload["most_likely_causal_drivers"][0]["name"] == "orders-web-asg"


def test_correlation_report_treats_non_positive_top_n_as_all_candidates() -> None:
    candidates = [
        UpstreamCandidate(
            name="orders-web-asg",
            tier="web",
            confidence=0.95,
            correlated_signals=(),
            rationale="Top candidate.",
        ),
        UpstreamCandidate(
            name="orders-worker-asg",
            tier="worker",
            confidence=0.2,
            correlated_signals=(),
            rationale="Lower candidate.",
        ),
    ]

    report = build_correlation_report(
        correlated_signals=(),
        ranked_candidates=candidates,
        top_n=0,
    )

    assert report.most_likely_causal_drivers == tuple(candidates)
