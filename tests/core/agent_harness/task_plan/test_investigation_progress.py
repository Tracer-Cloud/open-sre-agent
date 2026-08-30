"""Host advances TaskPlan while investigation stages run."""

from __future__ import annotations

from core.agent_harness.task_plan.investigation_progress import (
    advance_task_plan_for_investigation_node,
    advance_task_plan_to_phase,
    complete_task_plan,
    investigation_phase_index,
    pipeline_phase_to_step_index,
)
from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan, parse_task_plan


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
    assert investigation_phase_index("correlate_upstream") == 3
    assert investigation_phase_index("publish_findings") == 4
    assert investigation_phase_index("unknown_node") is None


def test_pipeline_phase_spreads_across_five_step_plans() -> None:
    # intake / gather / diagnose / correlate / publish → every step
    assert pipeline_phase_to_step_index(0, 5) == 0
    assert pipeline_phase_to_step_index(1, 5) == 1
    assert pipeline_phase_to_step_index(2, 5) == 2
    assert pipeline_phase_to_step_index(3, 5) == 3
    assert pipeline_phase_to_step_index(4, 5) == 4


def test_pipeline_phase_four_step_plan() -> None:
    assert pipeline_phase_to_step_index(0, 4) == 0
    assert pipeline_phase_to_step_index(1, 4) == 0
    assert pipeline_phase_to_step_index(2, 4) == 1
    assert pipeline_phase_to_step_index(3, 4) == 2
    assert pipeline_phase_to_step_index(4, 4) == 3


def test_advance_marks_prior_steps_completed() -> None:
    plan = _plan("in_progress", "pending", "pending", "pending")
    updated = advance_task_plan_to_phase(plan, 1)
    # On a 4-step plan, gather (phase 1) still maps to step 0.
    assert updated.steps[0].status is PlanStepStatus.IN_PROGRESS
    assert updated.current_index == 1


def test_advance_caps_publish_on_last_step_for_short_plans() -> None:
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
    updated = advance_task_plan_to_phase(plan, 4)
    assert updated.steps[0].status is PlanStepStatus.COMPLETED
    assert updated.steps[1].status is PlanStepStatus.COMPLETED
    assert updated.steps[2].status is PlanStepStatus.IN_PROGRESS
    assert updated.current_index == 3


def test_advance_five_step_fills_each_work_phase() -> None:
    five = TaskPlan(
        steps=(
            PlanStep(step="Scope", status=PlanStepStatus.IN_PROGRESS),
            PlanStep(step="Correlate", status=PlanStepStatus.PENDING),
            PlanStep(step="Rank", status=PlanStepStatus.PENDING),
            PlanStep(step="Propose", status=PlanStepStatus.PENDING),
            PlanStep(step="Verify", status=PlanStepStatus.PENDING),
        )
    )
    assert advance_task_plan_to_phase(five, 0).current_index == 1
    assert advance_task_plan_to_phase(five, 1).current_index == 2
    assert advance_task_plan_to_phase(five, 2).current_index == 3
    assert advance_task_plan_to_phase(five, 3).current_index == 4
    assert advance_task_plan_to_phase(five, 4).current_index == 5


def test_advance_skips_already_completed_target() -> None:
    plan = _plan("completed", "completed", "pending", "pending")
    updated = advance_task_plan_to_phase(plan, 0)
    assert updated.steps[0].status is PlanStepStatus.COMPLETED
    assert updated.steps[1].status is PlanStepStatus.COMPLETED
    assert updated.steps[2].status is PlanStepStatus.IN_PROGRESS


def test_advance_keeps_in_progress_when_mapped_target_is_completed() -> None:
    # Phase 2 maps to step 1 on a 4-step plan. Completing the live step and
    # leaving the rest pending would drop work-log attribution and point the
    # overlay at a phase that has not started.
    plan = _plan("in_progress", "completed", "pending", "pending")
    updated = advance_task_plan_to_phase(plan, 2)
    assert updated is plan
    assert updated.steps[0].status is PlanStepStatus.IN_PROGRESS
    assert updated.steps[1].status is PlanStepStatus.COMPLETED
    assert updated.steps[2].status is PlanStepStatus.PENDING


def test_advance_does_not_reset_completed_step_after_mapped_phase() -> None:
    plan = _plan("in_progress", "pending", "completed", "pending")
    updated = advance_task_plan_to_phase(plan, 2)
    assert updated.steps[0].status is PlanStepStatus.COMPLETED
    assert updated.steps[1].status is PlanStepStatus.IN_PROGRESS
    assert updated.steps[2].status is PlanStepStatus.COMPLETED
    assert updated.steps[3].status is PlanStepStatus.PENDING


def test_advance_does_not_regress() -> None:
    plan = _plan("completed", "in_progress", "pending", "pending")
    assert advance_task_plan_to_phase(plan, 0) is plan


def test_advance_is_idempotent_at_same_phase() -> None:
    plan = _plan("in_progress", "pending", "pending", "pending")
    assert advance_task_plan_to_phase(plan, 0) is plan


def test_advance_for_node_delegates() -> None:
    five = TaskPlan(
        steps=(
            PlanStep(step="Scope", status=PlanStepStatus.IN_PROGRESS),
            PlanStep(step="Gather", status=PlanStepStatus.PENDING),
            PlanStep(step="Rank", status=PlanStepStatus.PENDING),
            PlanStep(step="Propose", status=PlanStepStatus.PENDING),
            PlanStep(step="Verify", status=PlanStepStatus.PENDING),
        )
    )
    updated = advance_task_plan_for_investigation_node(five, "investigation_agent")
    assert updated.current_index == 2
    assert advance_task_plan_for_investigation_node(five, "nope") is five


def test_complete_marks_all_steps() -> None:
    plan = _plan("completed", "completed", "in_progress", "pending")
    done = complete_task_plan(plan)
    assert done.all_completed
    assert complete_task_plan(done) is done
