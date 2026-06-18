"""Execute second-phase terminal actions for CLI-agent turns."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from app.cli.interactive_shell.routing.handle_message_with_agent.errors import PlannerLLMError
from app.cli.interactive_shell.runtime import ReplSession

from .dispatch import execute_planned_actions
from .feedback import persist_error_turn, render_plan_denied, render_planner_llm_error
from .models import ActionExecutionDeps, ActionPlanningDecision, TerminalActionExecutionResult
from .planning import coerce_action_plan_decision, enforce_plan_fail_closed_policy


def _response_text_from_history_entries(entries: list[dict[str, Any]]) -> str:
    """Join the response text of executed history entries for prompt logging."""
    chunks: list[str] = []
    for item in entries:
        response_text = item.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            chunks.append(response_text.strip())
    return "\n".join(chunks)


def _resolve_plan(
    message: str,
    session: ReplSession,
    *,
    deps: ActionExecutionDeps | None,
    plan_actions_fn: Callable[[str, ReplSession], ActionPlanningDecision],
) -> ActionPlanningDecision:
    if deps is not None and deps.planner is not None:
        planned = deps.planner(message, session=session)
        return (
            ActionPlanningDecision((), True, True, ("planner_unavailable",))
            if planned is None
            else coerce_action_plan_decision(planned)
        )
    return coerce_action_plan_decision(plan_actions_fn(message, session))


def execute_cli_actions(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    plan_actions_fn: Callable[[str, ReplSession], ActionPlanningDecision],
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    deps: ActionExecutionDeps | None = None,
) -> bool:
    """Execute inferred actions from second-phase planning."""
    try:
        plan = _resolve_plan(
            message,
            session,
            deps=deps,
            plan_actions_fn=plan_actions_fn,
        )
    except PlannerLLMError as exc:
        error_text = str(exc)
        render_planner_llm_error(console, error_text)
        persist_error_turn(session, message, error_text)
        session.record("cli_agent", message, ok=False)
        return True

    plan = enforce_plan_fail_closed_policy(plan)
    actions = list(plan.actions)
    has_unhandled_clause = plan.has_unhandled_clause
    denied = plan.denied
    if denied:
        render_plan_denied(console)
        session.record("cli_agent", message, ok=False)
        return True
    if not actions:
        return False
    return execute_planned_actions(
        actions=actions,
        has_unhandled_clause=has_unhandled_clause,
        message=message,
        session=session,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        dispatch_fn=deps.dispatch if deps is not None else None,
    )


def execute_cli_actions_with_metrics(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    plan_actions_fn: Callable[[str, ReplSession], ActionPlanningDecision],
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    deps: ActionExecutionDeps | None = None,
) -> TerminalActionExecutionResult:
    """Execute planned actions and return per-turn action counters."""
    from app.analytics.cli import (
        capture_repl_execution_policy_decision,
        capture_terminal_actions_executed,
        capture_terminal_actions_planned,
    )

    try:
        plan = _resolve_plan(
            message,
            session,
            deps=deps,
            plan_actions_fn=plan_actions_fn,
        )
    except PlannerLLMError as exc:
        error_text = str(exc)
        render_planner_llm_error(console, error_text)
        persist_error_turn(session, message, error_text)
        session.record("cli_agent", message, ok=False)
        capture_terminal_actions_executed(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
        )
        return TerminalActionExecutionResult(0, 0, 0, True, True, response_text=error_text)

    plan = enforce_plan_fail_closed_policy(plan)
    actions = list(plan.actions)
    has_unhandled_clause = plan.has_unhandled_clause
    denied = plan.denied
    capture_terminal_actions_planned(
        planned_count=len(actions),
        has_unhandled_clause=has_unhandled_clause,
    )
    capture_repl_execution_policy_decision(
        {
            "policy_stage": "terminal_action_planning",
            "policy_trace": ",".join(plan.policy_trace),
            "planned_count": len(actions),
            "has_unhandled_clause": has_unhandled_clause,
            "denied": denied,
        }
    )
    if denied:
        response_text = (
            "I couldn't safely decide actions for that request. "
            "Please rephrase or use explicit slash commands."
        )
        render_plan_denied(console)
        session.record("cli_agent", message, ok=False, response_text=response_text)
        capture_terminal_actions_executed(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
        )
        return TerminalActionExecutionResult(0, 0, 0, True, True, response_text=response_text)
    if not actions:
        return TerminalActionExecutionResult(0, 0, 0, has_unhandled_clause, False)

    history_start = len(session.history)
    handled = execute_planned_actions(
        actions=actions,
        has_unhandled_clause=has_unhandled_clause,
        message=message,
        session=session,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        dispatch_fn=deps.dispatch if deps is not None else None,
    )
    executed_entries = [
        item
        for item in session.history[history_start:]
        if item.get("type")
        in {"slash", "shell", "alert", "synthetic_test", "implementation", "cli_command"}
    ]
    executed_count = len(executed_entries)
    executed_success_count = sum(1 for item in executed_entries if item.get("ok", True))
    response_text = _response_text_from_history_entries(executed_entries)
    capture_terminal_actions_executed(
        planned_count=len(actions),
        executed_count=executed_count,
        executed_success_count=executed_success_count,
    )
    return TerminalActionExecutionResult(
        len(actions),
        executed_count,
        executed_success_count,
        has_unhandled_clause,
        handled,
        response_text=response_text,
    )


__all__ = ["execute_cli_actions", "execute_cli_actions_with_metrics"]
