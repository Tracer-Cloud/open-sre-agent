"""Host advances TaskPlan while investigation stages run."""

from __future__ import annotations

from core.agent_harness.task_plan.investigation_progress import (
    advance_task_plan_for_investigation_node,
    advance_task_plan_to_phase,
    complete_task_plan,
    investigation_phase_index,
)
from core.agent_harness.task_plan.plan import PlanStepStatus, parse_task_plan


def _plan(*statuses: str):
    labels = [
        "Gather latency evidence",
        "Rank root-cause hypotheses",
        "Propose targeted fix",
        "Verify recovery",
    ]
    items = [{"step": labels[i], "status": status} for i, status in enumerate(statuses)]
    plan, error = parse_task_plan({"plan": items})
    assert error is None and plan is not None
    return plan


def test_investigation_phase_index_maps_pipeline_nodes() -> None:
    assert investigation_phase_index("resolve_integrations") == 0
    assert investigation_phase_index("extract_alert") == 0
    assert investigation_phase_index("plan_actions") == 0
    assert investigation_phase_index("investigation_agent") == 1
    assert investigation_phase_index("investigate_logs") == 1
    assert investigation_phase_index("diagnose_root_cause") == 2
    assert investigation_phase_index("correlate_upstream") == 2
    assert investigation_phase_index("publish_findings") == 3
    assert investigation_phase_index("unknown_node") is None


def test_advance_marks_prior_steps_completed() -> None:
    plan = _plan("in_progress", "pending", "pending", "pending")
    updated = advance_task_plan_to_phase(plan, 1)
    assert updated.steps[0].status is PlanStepStatus.COMPLETED
    assert updated.steps[1].status is PlanStepStatus.IN_PROGRESS
    assert updated.steps[2].status is PlanStepStatus.PENDING
    assert updated.current_index == 2


def test_advance_caps_at_last_step_for_short_plans() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Gather", "status": "in_progress"},
                {"step": "Diagnose", "status": "pending"},
                {"step": "Verify", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    updated = advance_task_plan_to_phase(plan, 3)
    assert updated.steps[0].status is PlanStepStatus.COMPLETED
    assert updated.steps[1].status is PlanStepStatus.COMPLETED
    assert updated.steps[2].status is PlanStepStatus.IN_PROGRESS
    assert updated.current_index == 3


def test_advance_does_not_regress() -> None:
    plan = _plan("completed", "in_progress", "pending", "pending")
    assert advance_task_plan_to_phase(plan, 0) is plan


def test_advance_is_idempotent_at_same_phase() -> None:
    plan = _plan("in_progress", "pending", "pending", "pending")
    assert advance_task_plan_to_phase(plan, 0) is plan


def test_advance_for_node_delegates() -> None:
    plan = _plan("in_progress", "pending", "pending", "pending")
    updated = advance_task_plan_for_investigation_node(plan, "investigation_agent")
    assert updated.current_index == 2
    assert advance_task_plan_for_investigation_node(plan, "nope") is plan


def test_complete_marks_all_steps() -> None:
    plan = _plan("completed", "completed", "in_progress", "pending")
    done = complete_task_plan(plan)
    assert done.all_completed
    assert complete_task_plan(done) is done
