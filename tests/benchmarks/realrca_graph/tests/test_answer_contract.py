from __future__ import annotations

from tests.benchmarks.realrca_graph.answer_contract import (
    assess_answer_contract,
    prompt_contract,
)
from tests.benchmarks.realrca_graph.models import (
    CandidateAnswer,
    EvidenceBundle,
    EvidenceItem,
    RootHypothesis,
)


def _bundle() -> EvidenceBundle:
    evidence = [
        EvidenceItem(
            id="e1",
            name="trace_get",
            modality="trace",
            summary="provider-app ProviderApi timeout",
        ),
        EvidenceItem(
            id="e2",
            name="metric_rt",
            modality="metric",
            summary="provider-app RT rose",
        ),
        EvidenceItem(
            id="e3",
            name="log_error",
            modality="log",
            summary="HSFTimeOutException on provider-app",
        ),
    ]
    return EvidenceBundle(
        case_id="case-1",
        split="test",
        case_type="HSF",
        data_ref="snapshot",
        ontology=[],
        retrieval_summary="",
        evidence=evidence,
        hypotheses=[
            RootHypothesis(
                id="h1",
                kind="trace_span",
                label="provider-app",
                root_layer="service_dependency",
                score=5.0,
                reason="ProviderApi timeout caused downstream success-rate drop",
                modalities=["trace", "metric", "log"],
                support=evidence,
            )
        ],
    )


def test_answer_contract_rewards_structured_grounded_rca() -> None:
    answer = CandidateAnswer(
        "candidate",
        "case-1",
        (
            "根因：provider-app 的 ProviderApi 超时导致 consumer-app HSF 成功率下跌。"
            "关键证据：Trace 显示 provider-app span 超时，指标显示 ProviderApi RT 上升，"
            "日志出现 HSFTimeOutException。影响链路：provider-app 处理变慢导致下游等待并触发告警。"
            "排除项：不是 consumer-app 单机故障。处置建议：优先隔离慢实例并排查 provider-app 下游。"
        ),
        "212a6a3417840231458777961e0d45",
    )

    assessment = assess_answer_contract(answer, _bundle())

    assert assessment.score >= 0.9
    assert "contract_incomplete_answer" not in assessment.flags
    assert assessment.mentioned_modalities == ["trace", "metric", "log"]


def test_answer_contract_flags_text_without_evidence_shape() -> None:
    answer = CandidateAnswer(
        "candidate",
        "case-1",
        "provider-app seems bad.",
        "212a6a3417840231458777961e0d45",
    )

    assessment = assess_answer_contract(answer, _bundle())

    assert "contract_missing_root_statement" in assessment.flags
    assert "contract_missing_observable_evidence" in assessment.flags
    assert "contract_low_evidence_modality_coverage" in assessment.flags
    assert assessment.score < 0.62


def test_prompt_contract_exposes_expected_sections_and_modalities() -> None:
    payload = prompt_contract(_bundle())

    assert "single concrete root cause sentence" in payload["required_sections"]
    assert payload["expected_modalities"] == ["trace", "metric", "log"]
    assert payload["top_root_options"][0]["label"] == "provider-app"
