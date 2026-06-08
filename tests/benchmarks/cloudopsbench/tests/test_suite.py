from __future__ import annotations

import pytest

from tests.benchmarks.cloudopsbench.case_loader import BENCHMARK_DIR, load_case, validate_corpus
from tests.benchmarks.cloudopsbench.replay_backend import CloudOpsBenchReplayBackend
from tests.benchmarks.cloudopsbench.scoring import score_case, summarize_scores

pytestmark = [
    pytest.mark.cloudopsbench,
    pytest.mark.skipif(
        not BENCHMARK_DIR.is_dir(),
        reason="CloudOpsBench benchmark data is not downloaded; run "
        "`make download-cloudopsbench-hf` first.",
    ),
]


def test_copied_corpus_validates() -> None:
    report = validate_corpus()

    assert report.ok, report.errors[:5]
    assert report.total_cases == 656
    assert report.slice_counts["boutique/service"] == 54
    assert report.slice_counts["trainticket/runtime"] == 96


def test_replay_backend_cache_hit_for_sample_case() -> None:
    case = load_case("boutique", "service", "1")
    backend = CloudOpsBenchReplayBackend(case)

    result = backend.GetResources("pods", namespace="boutique")

    assert result["available"] is True
    assert result["cache_hit"] is True
    assert result["action_name"] == "GetResources"
    assert result["action_input"]["resource_type"] == "pods"
    assert "cartservice" in str(result["output"])


def test_scoring_matches_reference_semantics_for_canned_trace() -> None:
    case = load_case("boutique", "service", "1")
    case_data = {
        "final_answer": {
            "top_3_predictions": [
                {
                    "rank": 1,
                    "fault_taxonomy": "Service_Routing_Fault",
                    "fault_object": "app/cartservice",
                    "root_cause": "service_env_var_address_mismatch",
                }
            ]
        },
        "steps": [
            {
                "step_id": 1,
                "action_type": "tool",
                "action_name": "GetResources",
                "action_input": {"resource_type": "pods"},
            },
            {
                "step_id": 2,
                "action_type": "tool",
                "action_name": "GetErrorLogs",
                "action_input": {"service_name": "frontend"},
            },
            {
                "step_id": 3,
                "action_type": "tool",
                "action_name": "GetResources",
                "action_input": {"resource_type": "services"},
            },
            {
                "step_id": 4,
                "action_type": "tool",
                "action_name": "GetServiceDependencies",
                "action_input": {"service_name": "frontend"},
            },
            {
                "step_id": 5,
                "action_type": "tool",
                "action_name": "GetAppYAML",
                "action_input": {"app_name": "cartservice"},
            },
        ],
    }

    score = score_case(case, case_data)
    summary = summarize_scores([score])

    assert score.metrics.a1 == 1.0
    assert score.metrics.a3 == 1.0
    assert score.metrics.any_order == 1.0
    assert score.metrics.cov == 1.0
    assert summary["metrics"]["Accuracy @1"] == 1.0
