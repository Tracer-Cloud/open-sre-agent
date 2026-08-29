from __future__ import annotations

from tests.benchmarks.realrca_graph.probe_feedback import (
    ProbeFeedbackLedger,
    case_suffix,
    probe_suffix,
)


def test_probe_suffix_accepts_short_and_321_prefixed_names() -> None:
    assert probe_suffix("probe-evidencegenv3-21f4") == "21f4"
    assert probe_suffix("probe-trajpatch-321f9") == "21f9"
    assert case_suffix("01a0330f-29a8-7e83-8121-3bf4cce321f4") == "21f4"


def test_feedback_ledger_groups_outcomes_against_best_accuracy() -> None:
    payload = {
        "items": [
            {
                "team_name": "隐元玩一玩",
                "agent_name": "probe-gselect-21f8",
                "accuracy": 84.85,
            },
            {
                "team_name": "隐元玩一玩",
                "agent_name": "probe-evidencegenv3-21f4",
                "accuracy": 81.82,
            },
            {
                "team_name": "someone else",
                "agent_name": "probe-evidencegenv3-21f4",
                "accuracy": 90.0,
            },
        ]
    }

    ledger = ProbeFeedbackLedger.from_leaderboard(payload, team_name="隐元玩一玩")

    assert ledger.reference_accuracy == 84.85
    feedback = ledger.cases["21f4"]
    assert feedback.negative_count == 1
    assert feedback.worst_delta == -3.03
    assert ledger.cases["21f8"].neutral_count == 1


def test_case_feedback_matches_negative_candidate_family() -> None:
    payload = {
        "items": [
            {
                "team_name": "隐元玩一玩",
                "agent_name": "probe-evidencegenv3-21f4",
                "accuracy": 81.82,
            },
            {
                "team_name": "隐元玩一玩",
                "agent_name": "probe-gselect-21f8",
                "accuracy": 84.85,
            },
        ]
    }
    ledger = ProbeFeedbackLedger.from_leaderboard(payload, team_name="隐元玩一玩")

    feedback = ledger.cases["21f4"]
    match = feedback.matching_negative("results-test-evidence-gen-v3-weak-risky")

    assert match is not None
    assert match.agent_name == "probe-evidencegenv3-21f4"
    assert feedback.matching_negative("results-test-new-strategy") is None


def test_case_feedback_matches_trajectory_patcher_alias() -> None:
    payload = {
        "items": [
            {
                "team_name": "隐元玩一玩",
                "agent_name": "probe-gselect-21f8",
                "accuracy": 84.85,
            },
            {
                "team_name": "隐元玩一玩",
                "agent_name": "probe-trajpatch-1d83",
                "accuracy": 81.82,
            },
        ]
    }
    ledger = ProbeFeedbackLedger.from_leaderboard(payload, team_name="隐元玩一玩")

    feedback = ledger.cases["1d83"]
    match = feedback.matching_negative("results-test-trajectory-evidence-patcher-v3-mixed10")

    assert match is not None
    assert match.agent_name == "probe-trajpatch-1d83"
