"""Advance a live :class:`TaskPlan` as investigation pipeline stages run.

``investigation_start`` blocks the action agent until the pipeline finishes, so
the model cannot call ``update_plan`` mid-run. The REPL host maps stream nodes
onto existing plan steps (ordinal, not text match) and writes statuses through
``apply_update_plan_session``.
"""

from __future__ import annotations

from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

# Pipeline phases before publish (0..3). Publish is a sentinel that always maps
# to the last checklist step (verify).
_PHASE_BY_NODE: dict[str, int] = {
    "resolve_integrations": 0,
    "extract_alert": 0,
    "plan_actions": 0,
    "investigation_agent": 1,
    "diagnose_root_cause": 2,
    "diagnose": 2,
    "opensre_llm_eval": 2,
    "correlate_upstream": 3,
    "merge_hypotheses": 3,
    "publish_findings": 4,
}
_WORK_PHASE_MAX = 3  # last work phase before publish
_PUBLISH_PHASE = 4


def investigation_phase_index(node_name: str) -> int | None:
    """Return the 0-based pipeline phase for a progress node, or ``None`` if unknown."""
    if node_name.startswith("investigate"):
        return 1
    return _PHASE_BY_NODE.get(node_name)


def pipeline_phase_to_step_index(phase: int, total_steps: int) -> int:
    """Map pipeline phase onto a checklist of ``total_steps`` steps.

    Work phases ``0..3`` spread evenly across steps ``0..total-2``; publish
    (phase 4) lands on the last (verify) step. A 5-step plan therefore gets
    intake→0, gather→1, diagnose→2, correlate→3, publish→4.
    """
    if total_steps <= 1:
        return 0
    clamped = max(0, min(int(phase), _PUBLISH_PHASE))
    if clamped >= _PUBLISH_PHASE:
        return total_steps - 1
    work_steps = total_steps - 1
    if work_steps <= 1:
        return 0
    return (clamped * (work_steps - 1)) // _WORK_PHASE_MAX


def advance_task_plan_to_phase(plan: TaskPlan, phase: int) -> TaskPlan:
    """Mark steps before the mapped phase completed and that step in_progress.

    Never moves the focus backwards: if an ``in_progress`` step is already ahead
    of the mapped step, or the plan is fully completed, returns ``plan``
    unchanged.

    Never reopens completed work. If the mapped step is already completed, keep
    the current ``in_progress`` step when one exists so the overlay and default
    work-log attribution stay on live work. When there is no active step,
    promote the next pending row so action activity is not dropped.
    """
    if not plan.steps or plan.all_completed:
        return plan
    target = pipeline_phase_to_step_index(phase, len(plan.steps))
    in_progress_at = next(
        (
            index
            for index, item in enumerate(plan.steps)
            if item.status is PlanStepStatus.IN_PROGRESS
        ),
        None,
    )
    if plan.steps[target].status is PlanStepStatus.COMPLETED:
        if in_progress_at is not None:
            return plan
        while (
            target < len(plan.steps) - 1 and plan.steps[target].status is PlanStepStatus.COMPLETED
        ):
            target += 1
    elif in_progress_at is not None and in_progress_at > target:
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
