"""Tests for core/orchestration/node/publish_findings — LLM judge invocation path."""

from __future__ import annotations

from typing import Any

import pytest

from core.domain.state import make_initial_state
from core.orchestration.node.publish_findings import deliver


def _make_state(*, evaluate: bool = False, rubric: str = "") -> dict[str, Any]:
    raw: dict[str, Any] = {"commonAnnotations": {"summary": "x"}}
    if rubric:
        raw["commonAnnotations"]["scoring_points"] = rubric

    state = make_initial_state(raw_alert=raw, opensre_evaluate=evaluate)
    return dict(state)


def _patch_generate_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.orchestration.node.publish_findings.generate_report",
        lambda _s: {"slack_message": "", "report": ""},
    )


def test_deliver_runs_judge_when_evaluate_and_rubric_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generate_report(monkeypatch)

    fake: dict[str, Any] = {
        "overall_pass": True,
        "score_0_100": 85,
        "rubric_items": [],
        "summary": "ok",
    }

    def mock_judge(*, state: dict[str, Any], rubric: str) -> dict[str, Any]:
        return fake

    monkeypatch.setattr(
        "integrations.opensre.llm_eval_judge.run_opensre_llm_judge",
        mock_judge,
    )

    state = _make_state(evaluate=True, rubric="test rubric")
    updates = deliver(state)
    assert updates["opensre_llm_eval"] == fake


def test_deliver_skips_judge_when_evaluate_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generate_report(monkeypatch)
    monkeypatch.setattr(
        "integrations.opensre.llm_eval_judge.run_opensre_llm_judge",
        lambda *_, **__: pytest.fail("judge should not be called"),
    )

    state = _make_state(evaluate=False, rubric="test rubric")
    state["opensre_eval_rubric"] = "test rubric"
    updates = deliver(state)
    assert not updates.get("opensre_llm_eval")


def test_deliver_sets_skip_on_judge_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generate_report(monkeypatch)

    def failing_judge(*, state: dict[str, Any], rubric: str) -> Any:
        raise RuntimeError("API timeout")

    monkeypatch.setattr(
        "integrations.opensre.llm_eval_judge.run_opensre_llm_judge",
        failing_judge,
    )

    state = _make_state(evaluate=True, rubric="test rubric")
    updates = deliver(state)
    ev = updates.get("opensre_llm_eval")
    assert ev is not None
    assert ev.get("skipped") is True
    assert "API timeout" in ev.get("reason", "")
