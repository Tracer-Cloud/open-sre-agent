"""Second-phase action planning for interactive-shell free text.

Routing has already decided that the turn belongs to the CLI agent. This module
decides whether the turn should execute explicit terminal actions before the
assistant falls back to a conversational answer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm_result,
)
from app.cli.interactive_shell.runtime import ReplSession

from .models import ActionPlanningDecision


def coerce_action_plan_decision(
    raw: ActionPlanningDecision
    | tuple[list[PlannedAction], bool]
    | tuple[list[PlannedAction], bool, bool],
) -> ActionPlanningDecision:
    """Back-compat adapter for tests that monkeypatch planning to tuple output."""
    if isinstance(raw, ActionPlanningDecision):
        return raw
    if len(raw) == 2:
        actions, has_unhandled_clause = raw
        denied = False
    else:
        actions, has_unhandled_clause, denied = raw
    return ActionPlanningDecision(
        actions=tuple(actions),
        has_unhandled_clause=has_unhandled_clause,
        denied=denied,
        policy_trace=(),
    )


def enforce_plan_fail_closed_policy(plan: ActionPlanningDecision) -> ActionPlanningDecision:
    if plan.denied:
        return plan
    actions = list(plan.actions)
    if not actions:
        return plan
    if all(action.kind == "assistant_handoff" for action in actions):
        if plan.has_unhandled_clause:
            return ActionPlanningDecision((), True, True, plan.policy_trace)
        return ActionPlanningDecision((), False, False, plan.policy_trace)
    if plan.has_unhandled_clause:
        return ActionPlanningDecision((), True, True, plan.policy_trace)
    return ActionPlanningDecision(tuple(actions), False, False, plan.policy_trace)


def plan_actions(
    message: str,
    session: ReplSession,
    *,
    planner: Callable[..., Any],
    default_planner: Callable[..., Any],
) -> ActionPlanningDecision:
    """Plan executable terminal actions for one CLI-agent turn."""
    # Fast path: `!cmd` is an explicit shell-passthrough prefix that must bypass
    # the LLM planner entirely.
    stripped = message.strip()
    if stripped.startswith("!") and len(stripped) > 1:
        from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
            shell_action,
        )

        cmd = " ".join(stripped[1:].split())  # normalise internal whitespace/newlines
        if cmd:
            return ActionPlanningDecision(
                actions=(shell_action(f"!{cmd}", 0),),
                has_unhandled_clause=False,
                denied=False,
                policy_trace=("deterministic_bang_shell",),
            )

    if planner is default_planner:
        llm_plan_result = plan_actions_with_llm_result(message, session=session)
        if llm_plan_result is None:
            return ActionPlanningDecision((), True, True, ("planner_unavailable",))
        actions = list(llm_plan_result.actions)
        has_unhandled_clause = llm_plan_result.has_unhandled_clause
        policy_trace = llm_plan_result.policy_trace
    else:
        # Preserve existing monkeypatch seam used by unit tests and debug harnesses.
        llm_plan_legacy = planner(message, session=session)
        if llm_plan_legacy is None:
            return ActionPlanningDecision((), True, True, ("planner_unavailable",))
        actions, has_unhandled_clause = llm_plan_legacy
        policy_trace = ()
    if not actions:
        return ActionPlanningDecision((), has_unhandled_clause, False, policy_trace)
    if all(action.kind == "assistant_handoff" for action in actions):
        if has_unhandled_clause:
            return ActionPlanningDecision((), True, True, policy_trace)
        return ActionPlanningDecision((), False, False, policy_trace)
    if has_unhandled_clause:
        return ActionPlanningDecision((), True, True, policy_trace)
    return ActionPlanningDecision(tuple(actions), False, False, policy_trace)


__all__ = [
    "coerce_action_plan_decision",
    "enforce_plan_fail_closed_policy",
    "plan_actions",
]
