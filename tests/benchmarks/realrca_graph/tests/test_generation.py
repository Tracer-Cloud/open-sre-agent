from __future__ import annotations

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.generation import (
    build_generation_package,
    extract_candidate_result,
    render_generation_prompt,
    sanitize_graph_analogues,
    sanitize_visible_tool_signals,
    validate_candidate_result,
)
from tests.benchmarks.realrca_graph.models import CandidateAnswer
from tests.benchmarks.realrca_graph.verifier import score_candidate


def test_generation_package_strips_hidden_case_fields_and_raw_refs() -> None:
    case = {
        "case_id": "case-1",
        "split": "test",
        "type": "HSF",
        "name": "hidden title",
        "reference": "hidden reference",
        "root_cause_chain": ["hidden root"],
        "meta": {"name": "hidden meta title", "alarm_id": "a-1"},
    }
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app:provider_group",
                    "score": 4.0,
                    "reason": "provider timeout",
                }
            ],
            "evidence": [
                {
                    "name": "trace_get",
                    "summary": "provider-app returned HSF-0001",
                    "raw_path": "/tmp/secret/path.json",
                }
            ],
        }
    )
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app HSF 超时。",
        "212a6a3417840231458777961e0d45",
    )
    score = score_candidate(baseline, baseline, bundle)

    package = build_generation_package(
        case=case,
        baseline=baseline,
        bundle=bundle,
        candidate_scores=[(baseline, score)],
        strategy_hint="优先测试深层下游慢调用，而不是只扩写当前答案。",
        frontier_context={
            "bucket": "raw_mechanism_probe",
            "raw_uncovered_mechanisms": ["hsf_threadpool_busy"],
            "graph_path": "/tmp/local/graph_context.json",
            "top_hypothesis": "provider-app THREADPOOL_BUSY",
            "blockers": ["case_negative_probe_history"],
        },
    )
    payload = package.to_dict()

    assert "name" not in payload["case"]
    assert "reference" not in payload["case"]
    assert "root_cause_chain" not in payload["case"]
    assert "name" not in payload["case"]["meta"]
    assert payload["answer_contract"]["expected_modalities"]
    assert "raw_ref" not in payload["evidence_bundle"]["top_hypotheses"][0]["support"][0]
    prompt = render_generation_prompt(package)
    assert "/tmp/secret" not in prompt
    assert "/tmp/local" not in prompt
    assert "evidence_bundle" not in prompt
    assert "top_hypotheses" not in prompt
    assert "frontier_differential" in prompt
    assert "graph_path" not in prompt
    assert payload["frontier_context"]["raw_uncovered_mechanisms"] == ["hsf_threadpool_busy"]
    assert '"id": "h1"' not in prompt
    assert "answer_contract" in prompt
    assert "possible_root_causes" in prompt
    assert payload["strategy_hint"] == "优先测试深层下游慢调用，而不是只扩写当前答案。"
    assert "本轮额外策略约束" in prompt
    assert "优先测试深层下游慢调用" in prompt


def test_generation_package_includes_answer_matched_system_entities() -> None:
    graph_context = {
        "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
        "nodes": [
            {"id": "alarm:a", "kind": "alarm", "label": "alarm-a"},
            {"id": "trace:t1", "kind": "trace", "label": "t1"},
            {"id": "span:s1", "kind": "span", "label": "com.demo.ProviderApi:query"},
            {"id": "app:consumer", "kind": "app", "label": "consumer-app"},
            {
                "id": "service:com.demo.ProviderApi",
                "kind": "service",
                "label": "com.demo.ProviderApi:query",
            },
        ],
        "edges": [
            {"source": "alarm:a", "rel": "MENTIONS", "target": "trace:t1"},
            {"source": "trace:t1", "rel": "HAS_SPAN", "target": "span:s1"},
            {"source": "span:s1", "rel": "INVOKES", "target": "service:com.demo.ProviderApi"},
            {
                "source": "app:consumer",
                "rel": "CALLS",
                "target": "service:com.demo.ProviderApi",
            },
        ],
        "root_candidates": [
            {
                "kind": "trace_span",
                "label": "com.demo.ProviderApi:query",
                "score": 4.0,
                "reason": "provider timeout",
            }
        ],
        "evidence": [{"name": "trace_get", "summary": "ProviderApi query timeout"}],
    }
    bundle = build_evidence_bundle(graph_context)
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：com.demo.ProviderApi query 超时。",
        "212a6a3417840231458777961e0d45",
    )

    package = build_generation_package(
        case={"case_id": "case-1", "split": "test", "type": "HSF"},
        baseline=baseline,
        bundle=bundle,
        candidate_scores=[(baseline, score_candidate(baseline, baseline, bundle))],
        graph_context=graph_context,
    )
    payload = package.to_dict()

    assert payload["matched_system_entities"]
    assert payload["matched_system_entities"][0]["kind"] == "service"
    neighbor_rels = {
        neighbor["rel"] for neighbor in payload["matched_system_entities"][0]["neighbors"]
    }
    assert "CALLS" in neighbor_rels
    assert payload["causal_path_hints"]
    assert payload["causal_path_hints"][0]["path_score"] > 0
    prompt = render_generation_prompt(package)
    assert "matched_system_entities" in prompt
    assert "causal_path_hints" in prompt
    assert "com.demo.ProviderApi" in prompt


def test_generation_package_includes_public_validation_exemplars() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app:provider_group",
                    "score": 4.0,
                    "reason": "provider timeout",
                }
            ],
            "evidence": [{"name": "trace_get", "summary": "provider-app timeout"}],
        }
    )
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app HSF 超时。",
        "212a6a3417840231458777961e0d45",
    )

    package = build_generation_package(
        case={"case_id": "case-1", "split": "test", "type": "HSF"},
        baseline=baseline,
        bundle=bundle,
        candidate_scores=[(baseline, score_candidate(baseline, baseline, bundle))],
        validation_exemplars=[
            {
                "case_id": "validation-1",
                "case_type": "HSF",
                "root_summary": "root_cause provider timeout",
            }
        ],
    )
    prompt = render_generation_prompt(package)

    assert package.to_dict()["validation_exemplars"][0]["case_id"] == "validation-1"
    assert "public_validation_exemplars" in prompt
    assert "只能参考故障模式" in prompt


def test_generation_package_includes_sanitized_visible_tool_signals() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "HSF"},
            "root_candidates": [
                {
                    "kind": "trace_span",
                    "label": "provider-app:provider_group",
                    "score": 4.0,
                    "reason": "provider timeout",
                }
            ],
            "evidence": [{"name": "trace_get", "summary": "provider-app timeout"}],
        }
    )
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：provider-app HSF 超时。",
        "212a6a3417840231458777961e0d45",
    )

    package = build_generation_package(
        case={"case_id": "case-1", "split": "test", "type": "HSF"},
        baseline=baseline,
        bundle=bundle,
        candidate_scores=[(baseline, score_candidate(baseline, baseline, bundle))],
        visible_tool_signals=[
            {
                "term": "HSF-0002",
                "kind": "hsf_code",
                "score": 24,
                "graph_supported": True,
                "source_runs": ["internal-run-name"],
                "event_counts": {"agent.tool_result": 3, "agent.message": 1},
                "occurrences": [{"snippet": "provider-app returned HSF-0002"}],
            }
        ],
    )
    prompt = render_generation_prompt(package)

    assert package.to_dict()["visible_tool_signals"][0]["tool_result_count"] == 3
    assert "additional_visible_observations" in prompt
    assert "HSF-0002" in prompt
    assert "provider-app returned HSF-0002" in prompt
    assert "internal-run-name" not in prompt


def test_generation_package_includes_sanitized_graph_analogues() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "Tair"},
            "root_candidates": [
                {
                    "kind": "pattern_cache_timeout",
                    "label": "redis r-abc timeout",
                    "score": 4.0,
                    "reason": "JedisConnectionException timeout",
                }
            ],
            "evidence": [{"name": "trace_get", "summary": "redis timeout"}],
        }
    )
    baseline = CandidateAnswer(
        "baseline",
        "case-1",
        "根因：redis r-abc 缓存超时。",
        "212a6a3417840231458777961e0d45",
    )

    package = build_generation_package(
        case={"case_id": "case-1", "split": "test", "type": "Tair"},
        baseline=baseline,
        bundle=bundle,
        candidate_scores=[(baseline, score_candidate(baseline, baseline, bundle))],
        graph_analogues=[
            {
                "case_id": "other-case",
                "case_type": "Tair",
                "analogue_role": "supporting_analogue",
                "similarity": 0.91,
                "mechanism_aligned": True,
                "matched_mechanisms": ["cache", "timeout"],
                "matched_root_kinds": ["pattern_cache_timeout"],
                "matched_layers": ["cache"],
                "matched_modalities": ["trace", "metric"],
                "matched_entities": ["app:other-app", "ip:33.1.2.3"],
                "matched_edges": ["app-CALLS->service"],
                "root_patterns": [
                    "redis rm-abc 33.1.2.3 trace 212a6a3417840231458777961e0d45 timeout"
                ],
                "negative_probe_count": 2,
            }
        ],
    )

    payload = package.to_dict()
    prompt = render_generation_prompt(package)

    assert payload["graph_analogues"][0]["negative_probe_count"] == 2
    assert payload["graph_analogues"][0]["analogue_role"] == "supporting_analogue"
    assert payload["graph_analogues"][0]["mechanism_aligned"] is True
    assert payload["graph_analogues"][0]["root_patterns"] == [
        "redis [rds] [ip] trace [trace] timeout"
    ]
    assert "graph_analogues" in prompt
    assert "结构一致性/风险提示" in prompt
    assert "negative_constraint" in prompt
    assert "other-case" not in prompt
    assert "33.1.2.3" not in prompt
    assert "212a6a3417840231458777961e0d45" in prompt


def test_sanitize_graph_analogues_drops_case_identity_fields() -> None:
    analogues = sanitize_graph_analogues(
        [
            {
                "case_id": "validation-secret",
                "graph_label": "latest-validation",
                "case_type": "HSF",
                "analogue_role": "negative_constraint",
                "similarity": 0.82,
                "mechanism_aligned": False,
                "matched_mechanisms": ["limit"],
                "matched_root_kinds": ["pattern_limit"],
                "root_patterns": ["provider 11.2.3.4 SentinelBlockException"],
            }
        ]
    )

    assert analogues == [
        {
            "case_type": "HSF",
            "analogue_role": "negative_constraint",
            "similarity": 0.82,
            "mechanism_aligned": False,
            "matched_mechanisms": ["limit"],
            "matched_root_kinds": ["pattern_limit"],
            "root_patterns": ["provider [ip] SentinelBlockException"],
        }
    ]


def test_sanitize_visible_tool_signals_bounds_snippets_and_drops_process_fields() -> None:
    signals = sanitize_visible_tool_signals(
        [
            {
                "term": "MpfSystemException",
                "kind": "exception",
                "score": 28,
                "graph_supported": True,
                "source_runs": ["dma-clean"],
                "event_counts": {"agent.tool_result": 1},
                "occurrences": [
                    {"snippet": "x" * 500},
                    {"snippet": "second"},
                    {"snippet": "third"},
                ],
            }
        ],
        snippet_chars=40,
    )

    assert signals == [
        {
            "term": "MpfSystemException",
            "kind": "exception",
            "score": 28,
            "graph_supported": True,
            "tool_result_count": 1,
            "message_count": 0,
            "snippets": ["x" * 40 + "...", "second"],
        }
    ]


def test_extract_candidate_result_uses_last_json_object() -> None:
    text = (
        "thinking...\n"
        '{"case_id": "old", "diagnosis_output": "old", "trace_id": "old"}\n'
        '{"case_id": "case-1", "diagnosis_output": "root", "trace_id": "trace"}'
    )

    result = extract_candidate_result(text)

    assert result == {"case_id": "case-1", "diagnosis_output": "root", "trace_id": "trace"}
    assert validate_candidate_result("case-1", result) is None
    assert validate_candidate_result("case-2", result) == "case_id mismatch: case-1 != case-2"


def test_extract_candidate_result_recovers_unescaped_quotes_in_diagnosis() -> None:
    text = """```json
{
  "case_id": "case-1",
  "diagnosis_output": "根因：Jedis.mget 超时，导致"2_item维度查询棱镜"RT99 上涨。",
  "trace_id": "21030cd817844920277998294e1127",
  "strategy": "evidence_rewrite",
  "decision_reason": "current_answer root boundary is wrong"
}
```"""

    result = extract_candidate_result(text)

    assert result is not None
    assert result["case_id"] == "case-1"
    assert result["trace_id"] == "21030cd817844920277998294e1127"
    assert '"2_item维度查询棱镜"' in result["diagnosis_output"]
