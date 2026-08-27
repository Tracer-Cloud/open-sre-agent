"""Host policy for ``update_plan`` — normalize model mistakes after Ask User."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.task_plan.display import promote_first_pending_step
from core.agent_harness.task_plan.plan import TaskPlan


def apply_update_plan_host_policy(
    plan: TaskPlan,
    *,
    plan_only_requested: bool,
    turn_user_message: str,
    session: Any,
) -> tuple[TaskPlan, bool]:
    """Return ``(plan, effective_plan_only)`` after Ask User rules.

    A user-originated plan-only restriction (the armed ``plan_only_until_authorized``
    latch, set either by ``ask_user_choice(plan_only_after=true)`` or a prior
    ``update_plan(plan_only=true)``) cannot be dropped by a model
    ``plan_only=false``. Ask User answers otherwise continue the original
    request — models often mis-set ``plan_only`` there.
    """
    ask_user_turn = bool(parse_ask_user_answers(turn_user_message))
    user_plan_only = bool(getattr(session, "plan_only_until_authorized", False))

    if ask_user_turn:
        if user_plan_only:
            effective_plan_only = True
        else:
            effective_plan_only = False
            if plan.all_pending:
                plan = promote_first_pending_step(plan)
        return plan, effective_plan_only

    return plan, plan_only_requested


def apply_update_plan_session(
    session: Any,
    plan: TaskPlan,
    *,
    plan_only: bool,
) -> None:
    """Persist plan and the plan-only latch from normalized policy output.

    The latch is set-only here: marking a step in_progress must NOT clear it, or
    the model could authorize its own execution. Only the user confirming a
    mutating step at the execution gate lifts the latch.
    """
    session.task_plan = plan
    if plan_only:
        session.plan_only_until_authorized = True


__all__ = [
    "apply_update_plan_host_policy",
    "apply_update_plan_session",
]
