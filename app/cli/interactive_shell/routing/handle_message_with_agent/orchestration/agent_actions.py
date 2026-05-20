"""Terminal action planning/execution for the interactive assistant."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.commands import (
    SLASH_COMMANDS,
    dispatch_slash,
    switch_llm_provider,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
    run_claude_code_implementation,
    run_opensre_cli_command,
    run_sample_alert,
    run_shell_command,
    run_synthetic_test,
    run_text_investigation,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_planner import (
    plan_cli_actions,
    plan_terminal_tasks,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    evaluate_llm_runtime_switch,
    evaluate_slash_tier,
    execution_allowed,
    resolve_slash_execution_tier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
)
from app.cli.interactive_shell.runtime import ReplSession, TaskKind, TaskRecord, TaskStatus
from app.cli.interactive_shell.ui import DIM, print_planned_actions
from app.cli.interactive_shell.ui.streaming import render_response_header


@dataclass(frozen=True)
class TerminalActionExecutionResult:
    planned_count: int
    executed_count: int
    executed_success_count: int
    has_unhandled_clause: bool
    handled: bool


def _plan_actions(message: str) -> tuple[list[PlannedAction], bool, bool]:
    """Plan actions for a free-text message using LLM-first planning.

    Used to wrap the call in a ``rich.Live`` spinner for in-place
    "thinking…" feedback, but ``Live``'s cursor manipulation fights
    the now-always-active ``patch_stdout`` context that the persistent
    REPL holds for the lifetime of the session (produces transient
    cursor-jump / erase-line residue on every action-planning call).
    The bottom-toolbar spinner started by :func:`_run_one_dispatch`
    already animates throughout the dispatch — including this planning
    phase — so the user still sees feedback; no separate in-place
    indicator is needed here.
    """
    llm_plan = plan_actions_with_llm(message)
    if llm_plan is None:
        return [], True, True
    actions, has_unhandled_clause = llm_plan
    if has_unhandled_clause or not actions:
        return [], True, True
    return actions, False, False


def _render_plan_denied(console: Console) -> None:
    console.print()
    render_response_header(console, "assistant")
    console.print(
        "[yellow]I couldn't safely decide actions for that request.[/] "
        "Please rephrase or use explicit slash commands."
    )


def _running_task_matches(session: ReplSession, target: str) -> list[TaskRecord]:
    running = [
        task
        for task in session.task_registry.list_recent(n=50)
        if task.status == TaskStatus.RUNNING
    ]
    if target == "synthetic_test":
        return [task for task in running if task.kind == TaskKind.SYNTHETIC_TEST]
    if target == "task":
        return running
    return []


def _resolve_task_cancel_target(
    target: str,
    session: ReplSession,
    console: Console,
) -> str | None:
    if target in {"synthetic_test", "task"}:
        matches = _running_task_matches(session, target)
        if not matches:
            console.print(
                f"[dim]no running {escape(target)} task found. use[/] [bold]/tasks[/bold]"
            )
            session.record("slash", f"/cancel {target}", ok=False)
            return None
        if len(matches) > 1:
            ids = ", ".join(task.task_id for task in matches)
            console.print(
                f"[yellow]multiple running tasks match {escape(target)}:[/] "
                f"{escape(ids)} [dim](run /cancel <id>)[/]"
            )
            session.record("slash", f"/cancel {target}", ok=False)
            return None
        return matches[0].task_id

    candidates = session.task_registry.candidates(target)
    if not candidates:
        console.print(f"[red]no task matches id:[/] {escape(target)}")
        session.record("slash", f"/cancel {target}", ok=False)
        return None
    if len(candidates) > 1:
        console.print(
            f"[red]ambiguous id prefix:[/] {escape(target)} "
            f"[dim]({len(candidates)} matches — use a longer prefix)[/]"
        )
        session.record("slash", f"/cancel {target}", ok=False)
        return None
    return candidates[0].task_id


def _execute_task_cancel_action(
    target: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> None:
    task_id = _resolve_task_cancel_target(target, session, console)
    if task_id is None:
        return

    command = f"/cancel {task_id}"
    cmd = SLASH_COMMANDS["/cancel"]
    tier = resolve_slash_execution_tier("/cancel", [task_id], cmd.execution_tier)
    policy = evaluate_slash_tier(tier)
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary=command,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=True,
    ):
        session.record("slash", command, ok=False)
        return

    console.print(f"[bold]$ {escape(command)}[/bold]")
    dispatch_slash(
        command,
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        policy_precleared=True,
    )


def _execute_planned_actions(
    *,
    actions: list[PlannedAction],
    has_unhandled_clause: bool,
    message: str,
    session: ReplSession,
    console: Console,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    console.print()
    render_response_header(console, "assistant")
    print_planned_actions(console, actions)
    if not has_unhandled_clause:
        session.record("cli_agent", message)

    for action in actions:
        # Multi-action plans: if the user pressed Esc / typed
        # ``/cancel`` between actions, the per-dispatch cancel event
        # is set on the ``StreamingConsole``. Skip the rest of the
        # plan so a "run all of these" plan doesn't keep marching
        # through after an explicit cancel. ``getattr`` with a default
        # keeps non-streaming consoles (used by the seeded-input
        # test path) working unchanged.
        if getattr(console, "cancel_requested", False):
            console.print(f"[{DIM}](remaining actions cancelled)[/]")
            break
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
        elif action.kind == "cli_command":
            run_opensre_cli_command(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            )
        elif action.kind == "task_cancel":
            _execute_task_cancel_action(
                action.content,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            )
        elif action.kind == "implementation":
            run_claude_code_implementation(
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
        elif action.kind == "investigation":
            run_text_investigation(
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


def execute_cli_actions(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    """Execute inferred actions from LLM-first planning.

    Returns True when the request was handled (including explicit fail-closed
    denials). Returns False only for legacy/test paths that pass through with no
    planned actions and no deny signal.
    """
    actions, has_unhandled_clause, denied = _plan_actions(message)
    if denied:
        _render_plan_denied(console)
        session.record("cli_agent", message, ok=False)
        return True
    if not actions:
        return False
    return _execute_planned_actions(
        actions=actions,
        has_unhandled_clause=has_unhandled_clause,
        message=message,
        session=session,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )


def execute_cli_actions_with_metrics(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
) -> TerminalActionExecutionResult:
    """Execute planned actions and return per-turn action counters.

    ``confirm_fn`` is forwarded to :func:`execute_cli_actions` so the
    interactive REPL can route mid-dispatch ``Proceed? [y/N]`` prompts
    through its active prompt_toolkit input instead of the stdlib
    ``input()`` (which deadlocks against the running ``prompt_async``).
    """
    from app.analytics.cli import (
        capture_terminal_actions_executed,
        capture_terminal_actions_planned,
    )

    actions, has_unhandled_clause, denied = _plan_actions(message)
    capture_terminal_actions_planned(
        planned_count=len(actions),
        has_unhandled_clause=has_unhandled_clause,
    )
    if denied:
        _render_plan_denied(console)
        session.record("cli_agent", message, ok=False)
        capture_terminal_actions_executed(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
        )
        return TerminalActionExecutionResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=True,
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
    handled = _execute_planned_actions(
        actions=actions,
        has_unhandled_clause=has_unhandled_clause,
        message=message,
        session=session,
        console=console,
        confirm_fn=confirm_fn,
    )
    executed_entries = [
        item
        for item in session.history[history_start:]
        if item.get("type") in {"slash", "shell", "alert", "synthetic_test", "implementation"}
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
