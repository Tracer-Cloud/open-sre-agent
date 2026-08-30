"""Advance a live :class:`TaskPlan` as investigation pipeline stages run.

``investigation_start`` blocks the action agent until the pipeline finishes, so
the model cannot call ``update_plan`` mid-run. The REPL host maps stream nodes
onto existing plan steps (ordinal, not text match) and writes statuses through
``apply_update_plan_session``.
"""

from __future__ import annotations

from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

# Coarse pipeline phases (0 = intake/plan, 1 = gather, 2 = diagnose, 3 = publish).
# Spread across the live checklist via :func:`pipeline_phase_to_step_index`.
_PHASE_BY_NODE: dict[str, int] = {
    "resolve_integrations": 0,
    "extract_alert": 0,
    "plan_actions": 0,
    "investigation_agent": 1,
    "diagnose_root_cause": 2,
    "diagnose": 2,
    "correlate_upstream": 2,
    "merge_hypotheses": 2,
    "opensre_llm_eval": 2,
    "publish_findings": 3,
}
_PIPELINE_PHASE_MAX = 3


def investigation_phase_index(node_name: str) -> int | None:
    """Return the 0-based pipeline phase for a progress node, or ``None`` if unknown."""
    if node_name.startswith("investigate"):
        return 1
    return _PHASE_BY_NODE.get(node_name)


def pipeline_phase_to_step_index(phase: int, total_steps: int) -> int:
    """Map pipeline phase ``0..3`` onto a checklist of ``total_steps`` steps.

    Phases 0–2 spread across the non-verify steps; phase 3 lands on the last
    (verify) step. A 5-step plan therefore gets intake→0, gather→1, diagnose→2,
    publish→4 — leaving step 3 for remediation narrative without stealing verify.
    """
    if total_steps <= 1:
        return 0
    clamped = max(0, min(int(phase), _PIPELINE_PHASE_MAX))
    if clamped >= _PIPELINE_PHASE_MAX:
        return total_steps - 1
    work_steps = total_steps - 1
    return min(work_steps - 1, (clamped * work_steps) // _PIPELINE_PHASE_MAX)


def advance_task_plan_to_phase(plan: TaskPlan, phase: int) -> TaskPlan:
    """Mark steps before the mapped phase completed and that step in_progress.

    Never moves the focus backwards: if the plan is already ahead of the mapped
    step, or already fully completed, returns ``plan`` unchanged.

    Never reopens completed work: a step that is already completed stays
    completed even when it sits at or after the mapped phase, so the overlay
    and work-log attribution do not jump onto finished steps.
    """
    if not plan.steps or plan.all_completed:
        return plan
    target = pipeline_phase_to_step_index(phase, len(plan.steps))
    focused = plan.current_index - 1
    if focused > target:
        return plan

    steps: list[PlanStep] = []
    for index, item in enumerate(plan.steps):
        if item.status is PlanStepStatus.COMPLETED or index < target:
            status = PlanStepStatus.COMPLETED
        elif index == target:
            status = PlanStepStatus.IN_PROGRESS
        else:
            status = PlanStepStatus.PENDING
        steps.append(PlanStep(step=item.step, status=status))
    updated = TaskPlan(steps=tuple(steps), explanation=plan.explanation)
    if updated.steps == plan.steps:
        return plan
    return updated


def advance_task_plan_for_investigation_node(plan: TaskPlan, node_name: str) -> TaskPlan:
    """Advance ``plan`` for a stream/progress node name."""
    phase = investigation_phase_index(node_name)
    if phase is None:
        return plan
    return advance_task_plan_to_phase(plan, phase)


def complete_task_plan(plan: TaskPlan) -> TaskPlan:
    """Mark every step completed (successful investigation finish)."""
    if not plan.steps or plan.all_completed:
        return plan
    steps = tuple(PlanStep(step=item.step, status=PlanStepStatus.COMPLETED) for item in plan.steps)
    return TaskPlan(steps=steps, explanation=plan.explanation)


__all__ = [
    "advance_task_plan_for_investigation_node",
    "advance_task_plan_to_phase",
    "complete_task_plan",
    "investigation_phase_index",
    "pipeline_phase_to_step_index",
]
