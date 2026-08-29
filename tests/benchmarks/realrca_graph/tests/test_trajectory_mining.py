from __future__ import annotations

from tests.benchmarks.realrca_graph.trajectory_mining import (
    TrajectoryObservation,
    answer_contains,
    extract_terms,
    mine_missing_terms,
)


def test_extract_terms_finds_exception_codes_and_services() -> None:
    text = (
        "MtopException class=java.lang.RuntimeException msg=SentinelBlockException by spo "
        "from com.alibaba.spo.mtop.opportunity.MtopOpportunityItemService:reportOpportunityItem "
        "caused by HSF-0001 and RDS rm-8vb678q3p3k66zikh"
    )

    terms = extract_terms(text)

    assert ("strong_phrase", "SentinelBlockException") in terms
    assert ("hsf_code", "HSF-0001") in terms
    assert ("rds", "rm-8vb678q3p3k66zikh") in terms
    assert any(
        kind == "java_service"
        and term.startswith("com.alibaba.spo.mtop.opportunity.MtopOpportunityItemService")
        for kind, term in terms
    )


def test_answer_contains_matches_simple_exception_name() -> None:
    assert answer_contains(
        "日志中出现 SentinelBlockException by spo。",
        "com.alibaba.csp.sentinel.slots.block.SentinelBlockException",
    )


def test_mine_missing_terms_filters_covered_terms_and_ranks_tool_results() -> None:
    observations = [
        TrajectoryObservation(
            source="dma",
            event="agent.tool_result",
            evidence_tier="tool_result",
            text=(
                "java.lang.RuntimeException: SentinelBlockException by spo from "
                "com.alibaba.spo.mtop.opportunity.MtopOpportunityItemService:reportOpportunityItem"
            ),
        ),
        TrajectoryObservation(
            source="dma",
            event="agent.message",
            evidence_tier="message",
            text="The final answer mentioned HSFTimeOutException as a symptom.",
        ),
    ]

    terms = mine_missing_terms(
        answer="当前答案已经覆盖 HSFTimeOutException。",
        observations=observations,
        graph_text="com.alibaba.spo.mtop.opportunity.MtopOpportunityItemService:reportOpportunityItem",
        min_score=1,
    )

    names = [term.term for term in terms]
    assert "SentinelBlockException" in names
    assert "HSFTimeOutException" not in names
    top = terms[0]
    assert top.event_counts["agent.tool_result"] == 1
    assert top.score >= 10
