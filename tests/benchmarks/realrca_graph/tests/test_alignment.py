from __future__ import annotations

from tests.benchmarks.realrca_graph.alignment import assess_alignment, critical_tokens
from tests.benchmarks.realrca_graph.models import CandidateAnswer


def test_critical_tokens_include_domain_entities_but_exclude_trace_id() -> None:
    answer = CandidateAnswer(
        "baseline",
        "case-1",
        (
            "provider-app 调用 com.demo.LockService:setNx 写 WS_GENERATE_LOCK，"
            "sql_id=da41ea70，trace 2131988117846860280378393d11e1。"
        ),
        "2131988117846860280378393d11e1",
    )

    tokens = critical_tokens(answer)

    assert "app:provider-app" in tokens
    assert "service:com.demo.lockservice:setnx" in tokens
    assert "sql_id:da41ea70" in tokens
    assert "term:ws_generate_lock" in tokens
    assert "trace:2131988117846860280378393d11e1" not in tokens


def test_assess_alignment_reports_dropped_baseline_entities() -> None:
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app com.alibaba.demo.ProviderApi:getThing HSFTimeOutException.",
        "trace-a",
    )
    candidate = CandidateAnswer(
        "candidate",
        "case-1",
        "payment-app rm-deadbeef slow SQL.",
        "trace-b",
    )

    assessment = assess_alignment(candidate, baseline)

    assert assessment.retention < 0.5
    assert "app:provider-app" in assessment.dropped_tokens
    assert any(
        token.startswith("service:com.alibaba.demo.providerapi")
        for token in assessment.dropped_tokens
    )
