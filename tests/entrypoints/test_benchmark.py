from __future__ import annotations

import json
from typing import Any

from _pytest.monkeypatch import MonkeyPatch

from app.entrypoints import benchmark as bench


def _fake_state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "root_cause": "node_selector_mismatch",
        "root_cause_category": "Scheduling_Fault",
        "validated_claims": [
            {"claim": "cartservice pod is Pending."},
            {"claim": "Node label disk-type=ssd does not match selector disktype=ssd."},
        ],
        "hypotheses": ["node_affinity_mismatch", "taint_toleration_mismatch"],
        "investigation_loop_count": 5,
        "available_action_names": ["k8s_describe"],
        "alert_json": {"service": "app/cartservice"},
        "alert_name": "cartservice down",
    }
    base.update(overrides)
    return base


def test_run_benchmark_case_happy_path(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.runners.run_investigation",
        lambda *a, **kw: _fake_state(),
    )

    result = bench.run_benchmark_case(
        bench.BenchmarkInputs(
            case_name="105",
            case_path="/cases/105",
            fault_category="scheduling",
            model="claude-sonnet-4-5-20250929",
            prompt_strategy="base",
            raw_alert={"alert_name": "cartservice down"},
        )
    )

    assert result.finished is True
    assert result.stop_reason == "final_answer"
    assert result.steps_used == 5
    assert result.case_name == "105"
    assert result.fault_category == "scheduling"
    assert result.model == "claude-sonnet-4-5-20250929"
    assert "cartservice" in result.key_evidence_summary
    assert len(result.top_3_predictions) == 3
    assert result.top_3_predictions[0].rank == 1
    assert result.top_3_predictions[0].fault_taxonomy == "Scheduling_Fault"
    assert result.top_3_predictions[0].fault_object == "app/cartservice"
    assert result.top_3_predictions[0].root_cause == "node_selector_mismatch"
    assert result.top_3_predictions[1].root_cause == "node_affinity_mismatch"
    assert result.top_3_predictions[2].root_cause == "taint_toleration_mismatch"


def test_run_benchmark_case_max_loops(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.runners.run_investigation",
        lambda *a, **kw: _fake_state(root_cause="", investigation_loop_count=99),
    )

    result = bench.run_benchmark_case(
        bench.BenchmarkInputs(case_name="x", case_path="/x", model="m")
    )

    assert result.finished is False
    assert result.stop_reason == "max_loops"


def test_run_benchmark_case_pipeline_error(monkeypatch: MonkeyPatch) -> None:
    def boom(*a: Any, **kw: Any) -> dict[str, Any]:
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr("app.pipeline.runners.run_investigation", boom)

    result = bench.run_benchmark_case(
        bench.BenchmarkInputs(case_name="x", case_path="/x", model="m")
    )

    assert result.finished is False
    assert result.stop_reason == "error"
    assert result.steps_used == 0
    assert result.top_3_predictions == []


def test_format_benchmark_block_round_trip(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.runners.run_investigation",
        lambda *a, **kw: _fake_state(),
    )

    result = bench.run_benchmark_case(
        bench.BenchmarkInputs(
            case_name="105",
            case_path="/cases/105",
            fault_category="scheduling",
            model="claude-sonnet-4-5-20250929",
        )
    )
    block = bench.format_benchmark_block(result)

    assert "Model         : claude-sonnet-4-5-20250929" in block
    assert "Fault Category: scheduling" in block
    assert "Stop Reason   : final_answer" in block
    assert "Final Answer:" in block

    final_answer_json = block.splitlines()[-1]
    parsed = json.loads(final_answer_json)
    assert "key_evidence_summary" in parsed
    assert len(parsed["top_3_predictions"]) == 3
    assert parsed["top_3_predictions"][0]["rank"] == 1
