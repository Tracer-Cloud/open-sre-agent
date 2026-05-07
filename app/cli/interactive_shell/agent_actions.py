"""Deterministic actions for the interactive terminal assistant."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.action_executor import (
    run_sample_alert,
    run_shell_command,
    run_synthetic_test,
)
from app.cli.interactive_shell.action_planner import (
    plan_actions_with_unhandled,
    plan_cli_actions,
    plan_terminal_tasks,
)
from app.cli.interactive_shell.command_registry import (
    SLASH_COMMANDS,
    dispatch_slash,
    switch_llm_provider,
)
from app.cli.interactive_shell.execution_policy import (
    evaluate_llm_runtime_switch,
    evaluate_slash_tier,
    execution_allowed,
    resolve_slash_execution_tier,
)
from app.cli.interactive_shell.rendering import print_planned_actions
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD


@dataclass(frozen=True)
class TerminalActionExecutionResult:
    planned_count: int
    executed_count: int
    executed_success_count: int
    has_unhandled_clause: bool
    handled: bool


def execute_cli_actions(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    """Execute inferred CLI and shell actions.

    Returns True when the message was handled. Unknown or ambiguous requests fall
    through to the LLM-backed assistant.
    """
    actions, has_unhandled_clause = plan_actions_with_unhandled(message)
    if not actions:
        return False

    console.print()
    console.print(f"[{TERMINAL_ACCENT_BOLD}]assistant:[/]")
    print_planned_actions(console, actions)
    if not has_unhandled_clause:
        session.record("cli_agent", message)

    for action in actions:
        console.print()
        if action.kind == "slash":
            stripped = action.content.strip()
            parts = stripped.split()
            if stripped == "/" or not parts:
                if not dispatch_slash(
                    action.content,
                    session,
                    console,
                    confirm_fn=confirm_fn,
                    is_tty=is_tty,
                ):
                    return True
                continue
            name = parts[0].lower()
            args = parts[1:]
            cmd = SLASH_COMMANDS.get(name)
            if cmd is None:
                if not dispatch_slash(
                    action.content,
                    session,
                    console,
                    confirm_fn=confirm_fn,
                    is_tty=is_tty,
                ):
                    return True
                continue
            tier = resolve_slash_execution_tier(name, args, cmd.execution_tier)
            policy = evaluate_slash_tier(tier)
            if not execution_allowed(
                policy,
                session=session,
                console=console,
                action_summary=stripped,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                action_already_listed=True,
            ):
                session.record("slash", stripped, ok=False)
                continue
            console.print(f"[bold]$ {escape(action.content)}[/bold]")
            if not dispatch_slash(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                policy_precleared=True,
            ):
                return True
        elif action.kind == "llm_provider":
            pol = evaluate_llm_runtime_switch(action_type="switch_llm_provider")
            if not execution_allowed(
                pol,
                session=session,
                console=console,
                action_summary=f"/model set {action.content}",
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                action_already_listed=True,
            ):
                continue
            console.print(f"[bold]$ /model set {escape(action.content)}[/bold]")
            ok = switch_llm_provider(action.content, console)
            session.record("slash", f"/model set {action.content}", ok=ok)
        elif action.kind == "shell":
            run_shell_command(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                action_already_listed=True,
            )
        elif action.kind == "sample_alert":
            run_sample_alert(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                action_already_listed=True,
            )
        else:
            run_synthetic_test(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                action_already_listed=True,
            )

    console.print()
    return not has_unhandled_clause


def execute_cli_actions_with_metrics(
    message: str, session: ReplSession, console: Console
) -> TerminalActionExecutionResult:
    """Execute deterministic actions and return per-turn action counters."""
    from app.analytics.cli import (
        capture_terminal_actions_executed,
        capture_terminal_actions_planned,
    )

    actions, has_unhandled_clause = plan_actions_with_unhandled(message)
    capture_terminal_actions_planned(
        planned_count=len(actions),
        has_unhandled_clause=has_unhandled_clause,
    )
    if not actions:
        return TerminalActionExecutionResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=has_unhandled_clause,
            handled=False,
        )

    history_start = len(session.history)
    handled = execute_cli_actions(message, session, console)
    executed_entries = [
        item
        for item in session.history[history_start:]
        if item.get("type") in {"slash", "shell", "alert", "synthetic_test"}
    ]
    executed_count = len(executed_entries)
    executed_success_count = sum(1 for item in executed_entries if item.get("ok", True))
    capture_terminal_actions_executed(
        planned_count=len(actions),
        executed_count=executed_count,
        executed_success_count=executed_success_count,
    )
    return TerminalActionExecutionResult(
        planned_count=len(actions),
        executed_count=executed_count,
        executed_success_count=executed_success_count,
        has_unhandled_clause=has_unhandled_clause,
        handled=handled,
    )


__all__ = [
    "TerminalActionExecutionResult",
    "execute_cli_actions",
    "execute_cli_actions_with_metrics",
    "plan_cli_actions",
    "plan_terminal_tasks",
]
