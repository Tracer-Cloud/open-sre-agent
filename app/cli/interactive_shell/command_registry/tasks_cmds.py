"""Slash commands: /history, /tasks, /cancel."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.history import load_command_history_entries
from app.cli.interactive_shell.rendering import repl_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.tasks import TaskKind, TaskRecord, TaskStatus
from app.cli.interactive_shell.theme import (
    ERROR,
    PRIMARY,
    TERMINAL_ACCENT_BOLD,
    TERMINAL_ERROR,
    TEXT_DIM,
    WARNING,
)


def _task_started_label(task: TaskRecord) -> str:
    return datetime.fromtimestamp(task.started_at, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _task_duration_label(task: TaskRecord) -> str:
    duration = task.duration_seconds()
    if duration is None:
        return "—"
    return f"{duration:.1f}s"


def _task_detail_label(task: TaskRecord) -> str:
    if task.error:
        return str(task.error)
    if task.result:
        return str(task.result)
    return "—"


def _cmd_history(_session: ReplSession, console: Console, _args: list[str]) -> bool:
    entries = load_command_history_entries()
    if not entries:
        console.print(f"[{TEXT_DIM}]no history yet.[/]")
        return True

    table = repl_table(title="Command history", title_style=TERMINAL_ACCENT_BOLD)
    table.add_column("#", style=TEXT_DIM, justify="right")
    table.add_column("text", overflow="fold")

    for i, entry in enumerate(entries, start=1):
        table.add_row(str(i), escape(entry))
    console.print(table)
    return True


def _cmd_tasks(session: ReplSession, console: Console, _args: list[str]) -> bool:
    tasks = session.task_registry.list_recent(n=50)
    if not tasks:
        console.print(f"[{TEXT_DIM}]no tasks recorded this session.[/]")
        return True

    table = repl_table(title="Tasks", title_style=TERMINAL_ACCENT_BOLD)
    table.add_column("id", style="bold")
    table.add_column("kind")
    table.add_column("status")
    table.add_column("started", style=TEXT_DIM)
    table.add_column("duration", style=TEXT_DIM, justify="right")
    table.add_column("detail", style=TEXT_DIM, overflow="fold")

    status_style = {
        TaskStatus.RUNNING: WARNING,
        TaskStatus.COMPLETED: PRIMARY,
        TaskStatus.CANCELLED: WARNING,
        TaskStatus.FAILED: ERROR,
        TaskStatus.PENDING: TEXT_DIM,
    }
    for task in tasks:
        st = status_style.get(task.status, TEXT_DIM)
        table.add_row(
            task.task_id,
            task.kind.value,
            f"[{st}]{task.status.value}[/{st}]",
            _task_started_label(task),
            _task_duration_label(task),
            escape(_task_detail_label(task)),
        )
    console.print(table)
    return True


def _cmd_cancel(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args:
        console.print(
            f"[{TERMINAL_ERROR}]usage:[/] /cancel <task_id>  — use [bold]/tasks[/bold] to list ids"
        )
        return True

    needle = args[0]
    candidates = session.task_registry.candidates(needle)
    if not candidates:
        console.print(f"[{TERMINAL_ERROR}]no task matches id:[/] {escape(needle)}")
        return True
    if len(candidates) > 1:
        console.print(
            f"[{TERMINAL_ERROR}]ambiguous id prefix:[/] {escape(needle)} "
            f"[{TEXT_DIM}]({len(candidates)} matches — use a longer prefix)[/]"
        )
        return True

    task = candidates[0]
    if task.status != TaskStatus.RUNNING:
        console.print(
            f"[{TEXT_DIM}]task {escape(task.task_id)} already finished "
            f"(status: {task.status.value}).[/]"
        )
        return True

    task.request_cancel()
    if task.kind == TaskKind.INVESTIGATION:
        console.print(
            f"[{WARNING}]cancellation signaled.[/] "
            f"[{TEXT_DIM}]if the investigation is still streaming, press[/] [bold]Ctrl+C[/bold] "
            f"[{TEXT_DIM}]to interrupt the current run.[/]"
        )
    else:
        console.print(
            f"[{PRIMARY}]stop requested[/] [{TEXT_DIM}]for synthetic test {escape(task.task_id)}.[/] "
            f"[{TEXT_DIM}]use[/] [bold]/tasks[/bold] [{TEXT_DIM}]to confirm status.[/]"
        )
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand("/history", "show persisted command history", _cmd_history),
    SlashCommand("/tasks", "list recent and in-flight shell tasks", _cmd_tasks),
    SlashCommand(
        "/cancel",
        "cancel a running task by id ('/cancel <task_id>' — see /tasks)",
        _cmd_cancel,
        execution_tier=ExecutionTier.ELEVATED,
    ),
]

__all__ = ["COMMANDS"]
