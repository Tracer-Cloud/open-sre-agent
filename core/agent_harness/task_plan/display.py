"""Terminal display helpers for live task plans."""

from __future__ import annotations

from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan


def is_plan_diagnosis_prose(text: str) -> bool:
    """True when prose repeats the Ask User facts/hypothesis block.

    The checklist and Ask User Q&A already show this; re-printing it as
    assistant markdown collapses into an unreadable wall of pipe characters.
    """
    stripped = text.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    has_facts = "facts:" in lowered or "established facts" in lowered
    has_hypothesis = "hypothesis" in lowered
    return (
        (has_facts and has_hypothesis)
        or (
            has_hypothesis
            and (
                "|" in stripped
                or ("primary" in lowered and "secondary" in lowered)
                or "ranked" in lowered
            )
        )
        or (has_facts and "leading hypothesis" in lowered)
    )


def promote_first_pending_step(plan: TaskPlan) -> TaskPlan:
    """Mark the first step in_progress when every step is still pending."""
    if not plan.all_pending or not plan.steps:
        return plan
    steps: list[PlanStep] = []
    for index, item in enumerate(plan.steps):
        if index == 0:
            steps.append(PlanStep(step=item.step, status=PlanStepStatus.IN_PROGRESS))
        else:
            steps.append(item)
    return TaskPlan(steps=tuple(steps), explanation=plan.explanation)


__all__ = [
    "is_plan_diagnosis_prose",
    "promote_first_pending_step",
]
