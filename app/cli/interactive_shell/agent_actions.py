"""Deterministic actions for the interactive terminal assistant."""

from __future__ import annotations

from collections.abc import Callable

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
from app.cli.interactive_shell.command_registry import dispatch_slash, switch_llm_provider
from app.cli.interactive_shell.execution_policy import (
    evaluate_llm_runtime_switch,
    execution_allowed,
)
from app.cli.interactive_shell.rendering import print_planned_actions
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD


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
    console.print()
    console.print("[dim]Running requested actions:[/dim]")
    if not has_unhandled_clause:
        session.record("cli_agent", message)

    for action in actions:
        console.print()
        if action.kind == "slash":
            console.print(f"[bold]$ {escape(action.content)}[/bold]")
            if not dispatch_slash(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            ):
                return True
        elif action.kind == "llm_provider":
            console.print(f"[bold]$ /model set {escape(action.content)}[/bold]")
            pol = evaluate_llm_runtime_switch(action_type="switch_llm_provider")
            if not execution_allowed(
                pol,
                session=session,
                console=console,
                action_summary=f"/model set {action.content}",
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            ):
                continue
            switch_llm_provider(action.content, console)
            session.record("slash", f"/model set {action.content}")
        elif action.kind == "shell":
            run_shell_command(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            )
        elif action.kind == "sample_alert":
            run_sample_alert(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            )
        else:
            run_synthetic_test(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            )

    console.print()
    return not has_unhandled_clause


__all__ = ["execute_cli_actions", "plan_cli_actions", "plan_terminal_tasks"]
