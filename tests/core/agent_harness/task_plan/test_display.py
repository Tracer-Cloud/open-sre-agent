"""Tests for task-plan terminal display helpers."""

from __future__ import annotations

from core.agent_harness.task_plan.display import is_plan_diagnosis_prose, promote_first_pending_step
from core.agent_harness.task_plan.plan import PlanStepStatus, parse_task_plan


def test_is_plan_diagnosis_prose_detects_established_facts_block() -> None:
    text = (
        "**Established facts**\n"
        "- Onset: gradual\n\n"
        "**Hypotheses**\n"
        "| Priority | Hypothesis | Why |\n"
        "| --- | --- | --- |"
    )
    assert is_plan_diagnosis_prose(text) is True


def test_is_plan_diagnosis_prose_detects_facts_hypothesis_table() -> None:
    text = (
        "Facts: gradual degradation on /api/orders.\n\n"
        "Hypothesis (ranked):\n"
        "| rank | hypothesis | why | next check |\n"
        "| --- | --- | --- | --- |"
    )
    assert is_plan_diagnosis_prose(text) is True


def test_is_plan_diagnosis_prose_ignores_short_explanation() -> None:
    assert is_plan_diagnosis_prose("Revised after deploy window narrowed.") is False


def test_promote_first_pending_step() -> None:
    plan, _error = parse_task_plan(
        {
            "plan": [
                {"step": "First", "status": "pending"},
                {"step": "Verify", "status": "pending"},
            ]
        }
    )
    assert plan is not None
    promoted = promote_first_pending_step(plan)
    assert promoted.steps[0].status is PlanStepStatus.IN_PROGRESS
    assert promoted.steps[1].status is PlanStepStatus.PENDING
