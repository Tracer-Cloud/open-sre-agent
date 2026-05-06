"""Slash commands: /history, /tasks, /cancel."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.history import load_command_history_entries
from app.cli.interactive_shell.rendering import repl_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.tasks import TaskKind, TaskRecord, TaskStatus
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD, TERMINAL_ERROR


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


def _cmd_history(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    entries = load_command_history_entries()
    if not entries:
        console.print("[dim]no history yet.[/dim]")
        return True

    table = repl_table(title="Command history", title_style=TERMINAL_ACCENT_BOLD)
    table.add_column("#", style="dim", justify="right")
    table.add_column("text", overflow="fold")

    for i, entry in enumerate(entries, start=1):
        table.add_row(str(i), escape(entry))
    console.print(table)
    return True


def _cmd_tasks(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    tasks = session.task_registry.list_recent(n=50)
    if not tasks:
        console.print("[dim]no tasks recorded this session.[/dim]")
        return True

    table = repl_table(title="Tasks", title_style=TERMINAL_ACCENT_BOLD)
    table.add_column("id", style="bold")
    table.add_column("kind")
    table.add_column("status")
    table.add_column("started", style="dim")
    table.add_column("duration", style="dim", justify="right")
    table.add_column("detail", style="dim", overflow="fold")

    status_style = {
        TaskStatus.RUNNING: "yellow",
        TaskStatus.COMPLETED: "green",
        TaskStatus.CANCELLED: "yellow",
        TaskStatus.FAILED: "red",
        TaskStatus.PENDING: "dim",
    }
    for task in tasks:
        st = status_style.get(task.status, "dim")
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
            f"[dim]({len(candidates)} matches — use a longer prefix)[/dim]"
        )
        return True

    task = candidates[0]
    if task.status != TaskStatus.RUNNING:
        console.print(
            f"[dim]task {escape(task.task_id)} already finished "
            f"(status: {task.status.value}).[/dim]"
        )
        return True

    task.request_cancel()
    if task.kind == TaskKind.INVESTIGATION:
        console.print(
            "[yellow]cancellation signaled.[/yellow] "
            "[dim]if the investigation is still streaming, press[/dim] [bold]Ctrl+C[/bold] "
            "[dim]to interrupt the current run.[/dim]"
        )
    else:
        console.print(
            f"[green]stop requested[/green] [dim]for synthetic test {escape(task.task_id)}.[/dim] "
            "[dim]use[/dim] [bold]/tasks[/bold] [dim]to confirm status.[/dim]"
        )
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand("/history", "show persisted command history", _cmd_history),
    SlashCommand("/tasks", "list recent and in-flight shell tasks", _cmd_tasks),
    SlashCommand(
        "/cancel",
        "cancel a running task by id ('/cancel <task_id>' — see /tasks)",
        _cmd_cancel,
    ),
]

__all__ = ["COMMANDS"]
