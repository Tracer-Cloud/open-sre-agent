"""Advance a live :class:`TaskPlan` as investigation pipeline stages run.

``investigation_start`` blocks the action agent until the pipeline finishes, so
the model cannot call ``update_plan`` mid-run. The REPL host maps stream nodes
onto existing plan steps (ordinal, not text match) and writes statuses through
``apply_update_plan_session``.
"""

from __future__ import annotations

from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

# Coarse phases aligned with typical 3–4 step investigation checklists.
# Cap at ``len(steps) - 1`` when applying so shorter plans still finish cleanly.
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


def investigation_phase_index(node_name: str) -> int | None:
    """Return the 0-based plan phase for a progress node, or ``None`` if unknown."""
    if node_name.startswith("investigate"):
        return 1
    return _PHASE_BY_NODE.get(node_name)


def advance_task_plan_to_phase(plan: TaskPlan, phase: int) -> TaskPlan:
    """Mark steps before ``phase`` completed and ``phase`` in_progress.

    Never moves the focus backwards: if the plan is already ahead of ``phase``,
    or already fully completed, returns ``plan`` unchanged.
    """
    if not plan.steps or plan.all_completed:
        return plan
    target = max(0, min(int(phase), len(plan.steps) - 1))
    focused = plan.current_index - 1
    if focused > target:
        return plan

    steps: list[PlanStep] = []
    for index, item in enumerate(plan.steps):
        if index < target:
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
]
