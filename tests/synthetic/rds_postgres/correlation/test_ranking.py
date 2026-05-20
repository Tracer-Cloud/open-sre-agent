from tests.synthetic.rds_postgres.correlation.models import UpstreamCandidate
from tests.synthetic.rds_postgres.correlation.ranking import rank_upstream_candidates


def test_candidate_ranking_is_deterministic() -> None:
    candidates = [
        UpstreamCandidate(
            name="orders-worker-asg",
            tier="worker",
            confidence=0.25,
            correlated_signals=(),
            rationale="Worker tier stayed mostly flat.",
        ),
        UpstreamCandidate(
            name="orders-web-asg",
            tier="web",
            confidence=0.91,
            correlated_signals=(),
            rationale="Web tier CPU rose with RDS CPU and DBConnections.",
        ),
        UpstreamCandidate(
            name="orders-api-asg",
            tier="web",
            confidence=0.91,
            correlated_signals=(),
            rationale="Tie-break should be stable by name.",
        ),
    ]

    ranked = rank_upstream_candidates(candidates)

    assert [candidate.name for candidate in ranked] == [
        "orders-api-asg",
        "orders-web-asg",
        "orders-worker-asg",
    ]
