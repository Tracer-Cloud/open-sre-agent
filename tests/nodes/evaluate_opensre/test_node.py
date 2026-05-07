from __future__ import annotations

from unittest.mock import patch

from app.nodes.evaluate_opensre.node import node_opensre_llm_eval


def test_node_opensre_llm_eval_skips_without_rubric() -> None:
    out = node_opensre_llm_eval({"opensre_eval_rubric": "", "opensre_evaluate": True})
    assert out["opensre_llm_eval"]["skipped"] is True


def test_node_opensre_llm_eval_calls_judge() -> None:
    with patch(
        "app.integrations.opensre.llm_eval_judge.run_opensre_llm_judge",
        return_value={"overall_pass": True, "score_0_100": 95},
    ):
        out = node_opensre_llm_eval(
            {
                "opensre_eval_rubric": "must cite latency",
                "root_cause": "latency",
                "evidence": {},
            }
        )
    assert out["opensre_llm_eval"]["overall_pass"] is True
    assert out["opensre_llm_eval"]["score_0_100"] == 95


def test_node_opensre_llm_eval_emits_parse_failed_metric(monkeypatch) -> None:
    captured_parse_failed: list[str] = []
    captured_failed: list[tuple[str, str]] = []

    def _capture_failed(
        *,
        duration_ms: float,
        failure_stage: str,
        failure_type: str,
        mode: str,
    ) -> None:
        _ = (duration_ms, mode)
        captured_failed.append((failure_stage, failure_type))

    monkeypatch.setattr(
        "app.nodes.evaluate_opensre.node.capture_eval_process_parse_failed",
        lambda *, failure_type, mode: captured_parse_failed.append(f"{failure_type}:{mode}"),
    )
    monkeypatch.setattr(
        "app.nodes.evaluate_opensre.node.capture_eval_process_failed",
        _capture_failed,
    )

    with patch(
        "app.integrations.opensre.llm_eval_judge.run_opensre_llm_judge",
        side_effect=ValueError("malformed json"),
    ):
        out = node_opensre_llm_eval(
            {
                "opensre_eval_rubric": "must cite latency",
                "root_cause": "latency",
                "evidence": {},
            }
        )

    assert "error" in out["opensre_llm_eval"]
    assert captured_parse_failed == ["ValueError:opensre_llm_judge"]
    assert captured_failed == [("parse_response", "ValueError")]
