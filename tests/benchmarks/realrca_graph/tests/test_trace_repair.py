from __future__ import annotations

from tests.benchmarks.realrca_graph.models import CandidateAnswer
from tests.benchmarks.realrca_graph.trace_repair import is_real_trace_id, repair_trace_id


def test_repair_trace_id_uses_trace_span_candidate_over_alarm_like_hex() -> None:
    answer = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app HSF method com.demo.Api@getThing timed out.",
        "codex-synthetic-id",
    )
    graph_context = {
        "root_candidates": [
            {
                "kind": "trace_span",
                "label": "provider-app:provider_group",
                "score": 3.5,
                "props": {
                    "trace_id": "212a6a3417840231458777961e0d45",
                    "server": "provider-app:provider_group",
                    "service": "com.demo.Api:1.0.0@getThing~P",
                },
            }
        ],
        "evidence": [
            {
                "name": "alarm",
                "command": "sf alarm get 46b62314809641bf80bafca2e3fa80c0",
                "summary": "alarm id 46b62314809641bf80bafca2e3fa80c0",
            }
        ],
    }

    repaired = repair_trace_id(answer, graph_context, allow_inferred=True)

    assert repaired.trace_id == "212a6a3417840231458777961e0d45"
    assert repaired.diagnosis_output == answer.diagnosis_output


def test_repair_trace_id_defaults_to_no_inferred_replacement() -> None:
    answer = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app HSF method com.demo.Api@getThing timed out.",
        "codex-synthetic-id",
    )
    graph_context = {
        "root_candidates": [
            {
                "kind": "trace_span",
                "label": "provider-app:provider_group",
                "score": 3.5,
                "props": {
                    "trace_id": "212a6a3417840231458777961e0d45",
                    "service": "com.demo.Api:1.0.0@getThing~P",
                },
            }
        ]
    }

    assert repair_trace_id(answer, graph_context) == answer


def test_repair_trace_id_preserves_real_existing_trace_id() -> None:
    answer = CandidateAnswer(
        "baseline",
        "case-1",
        "provider-app timeout.",
        "2131988117846860280378393d11e1",
    )

    assert is_real_trace_id(answer.trace_id)
    assert repair_trace_id(answer, {"root_candidates": []}) == answer


def test_repair_trace_id_keeps_answer_when_graph_has_no_trace_span() -> None:
    answer = CandidateAnswer(
        "baseline",
        "case-1",
        "database slow sql.",
        "fallback",
    )

    repaired = repair_trace_id(
        answer,
        {
            "root_candidates": [
                {
                    "kind": "sql",
                    "label": "rm-abc slow SQL",
                    "props": {"sql_id": "sql_123"},
                }
            ]
        },
    )

    assert repaired == answer


def test_repair_trace_id_rejects_unrelated_trace_for_sql_answer() -> None:
    answer = CandidateAnswer(
        "baseline",
        "case-1",
        "ori-forefront rm-8vb678q3p3k66zikh sql_id=da41ea70 progress_case slow SQL.",
        "codex-synthetic-id",
    )
    graph_context = {
        "root_candidates": [
            {
                "kind": "trace_span",
                "label": "timeoutcenter:timeoutcenterhost",
                "score": 4.5,
                "props": {
                    "trace_id": "213e068b17861182666684471e1086",
                    "server": "timeoutcenter:timeoutcenterhost",
                    "service": "Notify@recv~BytesMessage:TRADE:tc-refund-success",
                },
            }
        ]
    }

    assert repair_trace_id(answer, graph_context) == answer


def test_repair_trace_id_rejects_cache_read_trace_for_write_answer() -> None:
    answer = CandidateAnswer(
        "baseline",
        "case-1",
        "fliggy-alime-gateway Tair 写成功率下降，实例 0b4d327051f446d7 写请求超时。",
        "codex-synthetic-id",
    )
    graph_context = {
        "root_candidates": [
            {
                "kind": "trace_span",
                "label": "(tair@0b4d327051f446d7:tair.ldb.trip1a)",
                "score": 4.5,
                "props": {
                    "trace_id": "214156b117859321560051571d0f6d",
                    "client": "fliggy-alime-gateway:fliggy-alime-gateway_default_host",
                    "server": "(tair@0b4d327051f446d7:tair.ldb.trip1a)",
                    "service": "GET:0b4d327051f446d7:tair.ldb.trip1a:1939",
                    "result_code": "-3989",
                },
            }
        ]
    }

    assert repair_trace_id(answer, graph_context) == answer
