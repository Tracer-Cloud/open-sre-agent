from core.domain.correlation.confidence import (
    EvidenceContribution,
    build_weighted_confidence,
)
from core.domain.correlation.scoring import rank_upstream_candidates
from core.domain.types.upstream import UpstreamCandidate
from tools.investigation.reporting.upstream_correlation.feature_workflow import (
    score_feature_workflow_hypothesis,
)


def test_feature_workflow_matches_hint() -> None:
    result = score_feature_workflow_hypothesis(
        candidate_name="checkout-web",
        candidate_keywords=("checkout", "web"),
        operator_hints=("new checkout workflow deployed",),
    )

    assert result.score == 1.0
    assert result.matched_hints


def test_weighted_confidence_builds_score() -> None:
    confidence = build_weighted_confidence(
        (
            EvidenceContribution(
                source="correlation",
                score=1.0,
                weight=0.5,
                rationale="match",
            ),
            EvidenceContribution(
                source="topology",
                score=1.0,
                weight=0.5,
                rationale="adjacent",
            ),
        )
    )

    assert confidence.score == 1.0
    assert confidence.label == "high"


def test_feature_workflow_hint_changes_ranking() -> None:
    unhinted = UpstreamCandidate(
        name="orders-worker.latency",
        tier="application",
        confidence=0.70,
        correlated_signals=(),
        rationale="same correlation evidence",
    )
    hinted = UpstreamCandidate(
        name="checkout-web.latency",
        tier="application",
        confidence=0.71,
        correlated_signals=(),
        rationale="feature_workflow=1.0",
        evidence_breakdown=(
            {
                "source": "feature_workflow",
                "score": 1.0,
                "weight": 0.15,
                "rationale": "matched scheduled checkout workflow hint",
            },
        ),
    )

    ranked = rank_upstream_candidates([unhinted, hinted])

    assert ranked[0].name == "checkout-web.latency"
    assert ranked[0].evidence_breakdown[0]["source"] == "feature_workflow"
