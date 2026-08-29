from __future__ import annotations

from tests.benchmarks.realrca_graph.models import CandidateAnswer
from tests.benchmarks.realrca_graph.validation import score_validation_answer


def test_validation_score_rewards_critical_item_overlap() -> None:
    truth = {
        "root_cause_chain": ["provider-app Sentinel throttling"],
        "reference": {
            "required_items": [
                {"name": "root", "critical": True, "text": "provider-app Sentinel throttling"},
                {"name": "impact", "critical": False, "text": "consumer-app success rate drop"},
            ]
        },
    }
    answer = CandidateAnswer(
        "candidate",
        "case-1",
        "provider-app hit Sentinel throttling, causing consumer-app success rate drop.",
        "trace",
    )

    score = score_validation_answer(answer, truth, case_type="HSF")

    assert score.critical_coverage == 1.0
    assert score.loose_score > 0.7


def test_validation_score_lists_missing_critical_items() -> None:
    truth = {
        "root_cause_chain": ["rm-abc slow SQL"],
        "reference": {
            "required_items": [
                {"name": "database root", "critical": True, "text": "rm-abc slow SQL"},
            ]
        },
    }
    answer = CandidateAnswer("candidate", "case-1", "provider-app timeout only.", "trace")

    score = score_validation_answer(answer, truth, case_type="TDDL")

    assert score.critical_coverage == 0.0
    assert score.missing_critical_items == ["database root"]
