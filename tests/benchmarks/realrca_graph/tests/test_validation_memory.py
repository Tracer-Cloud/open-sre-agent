from __future__ import annotations

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.validation_memory import match_validation_exemplars


def test_match_validation_exemplars_prefers_same_type_and_entity_overlap() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-test", "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "hsf_service_method",
                    "label": "mainring:com.taobao.trade.SellerQueryService#queryCount",
                    "score": 6.0,
                    "reason": "HSF TCException fast reject",
                }
            ],
            "evidence": [
                {
                    "name": "trace_list",
                    "summary": "mainring SellerQueryService queryCount TCException",
                },
                {
                    "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                    "summary": "mainring SellerQueryService queryCount error qps rising",
                },
            ],
        }
    )
    memory = {
        "entries": [
            {
                "case_id": "validation-hsf",
                "case_type": "HSF",
                "feature_tokens": [
                    "service:com.taobao.trade.sellerqueryservice",
                    "method:querycount",
                    "term:tcexception",
                ],
                "truth": {
                    "root_cause_chain": [
                        {
                            "type": "root_cause",
                            "description": "下游接口触发 TC 限流",
                            "component": {"name": "tradeplatform3", "type": "app"},
                        }
                    ]
                },
                "graph": {"retrieval_summary": "service/method error_qps spike"},
            },
            {
                "case_id": "validation-tddl",
                "case_type": "TDDL",
                "feature_tokens": ["sql_table:orders"],
                "truth": {"root_cause_chain": []},
            },
        ]
    }

    matches = match_validation_exemplars(bundle, memory)

    assert [item.case_id for item in matches] == ["validation-hsf"]
    assert matches[0].case_type == "HSF"
    assert "TC 限流" in matches[0].root_summary
    assert "method:querycount" in matches[0].matched_terms


def test_match_validation_exemplars_returns_empty_without_memory() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-test", "split": "test", "type": "HSF"},
            "root_candidates": [],
            "evidence": [],
        }
    )

    assert match_validation_exemplars(bundle, None) == []


def test_match_validation_exemplars_uses_public_truth_mechanism_tokens() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-test", "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "hsf_service_method",
                    "label": "mainring:SellerQueryService#queryCount",
                    "score": 6.0,
                    "reason": "TCException fast reject",
                }
            ],
            "evidence": [{"name": "trace_get", "summary": "TCException 快速失败"}],
        }
    )
    memory = {
        "entries": [
            {
                "case_id": "validation-limit",
                "case_type": "HSF",
                "feature_tokens": ["app:alibaba-inc", "app:center-zb"],
                "truth": {
                    "root_cause_chain": [
                        {
                            "type": "root_cause",
                            "description": "接口 QPS 突增触发 Sentinel 限流",
                            "component": {"name": "provider", "type": "app"},
                        }
                    ]
                },
                "graph": {"retrieval_summary": ""},
            }
        ]
    }

    matches = match_validation_exemplars(bundle, memory)

    assert [item.case_id for item in matches] == ["validation-limit"]
    assert "keyword:limit" in matches[0].matched_terms
