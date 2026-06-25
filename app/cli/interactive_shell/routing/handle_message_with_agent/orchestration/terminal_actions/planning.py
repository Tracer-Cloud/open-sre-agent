"""Second-phase action planning for interactive-shell free text.

Routing has already decided that the turn belongs to the CLI agent. This module
decides whether the turn should execute explicit terminal actions before the
assistant falls back to a conversational answer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm_result,
)
from app.cli.interactive_shell.runtime import ReplSession

from .models import ActionPlanningDecision


def normalize_terminal_plan(plan: ActionPlanningDecision) -> ActionPlanningDecision:
    """Reduce a plan to its executable terminal actions.

    v0.1 removes the planning-stage fail-closed safeguard entirely: because every
    terminal action is read-only, an unmatched or ambiguous clause never warrants
    blocking the turn. We simply drop ``assistant_handoff`` markers (the assistant
    answers conversationally when no terminal action remains) and never deny.
    """
    executable = tuple(action for action in plan.actions if action.kind != "assistant_handoff")
    return ActionPlanningDecision(executable, False, plan.policy_trace)


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
                policy_trace=("deterministic_bang_shell",),
            )

    if planner is default_planner:
        llm_plan_result = plan_actions_with_llm_result(message, session=session)
        if llm_plan_result is None:
            # Planner unavailable: hand off to the conversational assistant rather
            # than denying the turn (v0.1 has no planning-stage fail-closed).
            return ActionPlanningDecision((), False, ("planner_unavailable",))
        actions = list(llm_plan_result.actions)
        policy_trace = llm_plan_result.policy_trace
    else:
        planned = planner(message, session=session)
        if planned is None:
            return ActionPlanningDecision((), False, ("planner_unavailable",))
        if not isinstance(planned, ActionPlanningDecision):
            msg = "planner must return ActionPlanningDecision or None"
            raise TypeError(msg)
        actions = list(planned.actions)
        policy_trace = planned.policy_trace

    executable = [action for action in actions if action.kind != "assistant_handoff"]
    return ActionPlanningDecision(tuple(executable), False, policy_trace)


__all__ = [
    "normalize_terminal_plan",
    "plan_actions",
]
