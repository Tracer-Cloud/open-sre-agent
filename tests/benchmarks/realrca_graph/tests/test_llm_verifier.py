from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.llm_verifier import (
    build_pairwise_verifier_package,
    extract_pairwise_verifier_result,
    parse_pairwise_verifier_decision,
    should_accept_pairwise_decision,
)
from tests.benchmarks.realrca_graph.models import CandidateAnswer, CandidateScore


def _score(
    source: str,
    *,
    support: float,
    risks: list[str] | None = None,
    retention: float = 1.0,
) -> CandidateScore:
    return CandidateScore(
        source=source,
        graph_support=support,
        answer_contract_score=1.0,
        best_hypothesis_id="h1",
        best_hypothesis_label="service-a#method",
        overlap_count=5,
        modality_count=3,
        novelty=0.2,
        baseline_retention=retention,
        dropped_baseline_tokens=[],
        risk_flags=risks or [],
    )


def test_pairwise_verifier_prompt_strips_hidden_case_fields() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "service-a#method",
                    "score": 6.0,
                    "reason": "trace timeout",
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "command": "sf trace get abc -f json",
                    "summary": "service-a method timeout",
                }
            ],
        }
    )
    package = build_pairwise_verifier_package(
        case={"case_id": "case-1", "root_cause_chain": "hidden", "meta": {"name": "hidden"}},
        baseline=CandidateAnswer("base", "case-1", "old answer", "abc"),
        candidate=CandidateAnswer("new", "case-1", "new answer", "abc"),
        bundle=bundle,
        baseline_score=_score("base", support=0.5),
        candidate_score=_score("new", support=0.7),
        previous_probe_agents=["probe-a"],
    )

    payload = json.dumps(package.to_dict(), ensure_ascii=False)

    assert "hidden" not in payload
    assert package.current_score["graph_support"] == 0.5
    assert package.challenger_score["graph_support"] == 0.7


def test_extract_pairwise_verifier_result_reads_last_json_object() -> None:
    result = extract_pairwise_verifier_result(
        'analysis {"verdict": "current"}\n{"case_id":"case-1","verdict":"challenger","confidence":0.81}'
    )

    assert result == {"case_id": "case-1", "verdict": "challenger", "confidence": 0.81}


def test_should_accept_high_confidence_material_candidate() -> None:
    decision = parse_pairwise_verifier_decision(
        "case-1",
        {
            "case_id": "case-1",
            "verdict": "challenger",
            "confidence": 0.8,
            "baseline_has_material_error": True,
            "candidate_preserves_baseline_root": True,
            "reason": "candidate fixes root boundary",
        },
    )

    accepted, reason = should_accept_pairwise_decision(
        decision=decision,
        baseline_score=_score("base", support=0.5),
        candidate_score=_score("new", support=0.61),
    )

    assert accepted is True
    assert reason == "accepted_by_pairwise_verifier"


def test_stable_baseline_needs_large_margin_even_with_material_error() -> None:
    decision = parse_pairwise_verifier_decision(
        "case-1",
        {
            "case_id": "case-1",
            "verdict": "challenger",
            "confidence": 0.86,
            "baseline_has_material_error": True,
            "candidate_preserves_baseline_root": True,
        },
    )

    accepted, reason = should_accept_pairwise_decision(
        decision=decision,
        baseline_score=_score("base", support=0.72),
        candidate_score=_score("new", support=0.91),
    )

    assert accepted is False
    assert reason == "stable_baseline_requires_large_support_margin"


def test_hard_risk_blocks_llm_verifier_acceptance() -> None:
    decision = parse_pairwise_verifier_decision(
        "case-1",
        {
            "case_id": "case-1",
            "verdict": "challenger",
            "confidence": 0.95,
            "baseline_has_material_error": True,
            "candidate_preserves_baseline_root": True,
        },
    )

    accepted, reason = should_accept_pairwise_decision(
        decision=decision,
        baseline_score=_score("base", support=0.4),
        candidate_score=_score("new", support=0.9, risks=["adds_secondary_trace_ids"]),
    )

    assert accepted is False
    assert reason == "candidate_has_hard_risk"


def test_evidence_only_expansion_blocks_llm_verifier_acceptance() -> None:
    decision = parse_pairwise_verifier_decision(
        "case-1",
        {
            "case_id": "case-1",
            "verdict": "challenger",
            "confidence": 0.95,
            "baseline_has_material_error": True,
            "candidate_preserves_baseline_root": True,
        },
    )

    accepted, reason = should_accept_pairwise_decision(
        decision=decision,
        baseline_score=_score("base", support=0.4),
        candidate_score=_score("new", support=0.9, risks=["likely_evidence_only_expansion"]),
    )

    assert accepted is False
    assert reason == "candidate_has_hard_risk"
