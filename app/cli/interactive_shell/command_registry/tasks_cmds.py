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
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
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
    if task.command:
        return str(task.command)
    return "—"


def _cmd_history(_session: ReplSession, console: Console, _args: list[str]) -> bool:
    entries = load_command_history_entries()
    if not entries:
        console.print(f"[{DIM}]no history yet.[/]")
        return True

    table = repl_table(title="Command history", title_style=BOLD_BRAND)
    table.add_column("#", style=DIM, justify="right")
    table.add_column("text", overflow="fold")

    for i, entry in enumerate(entries, start=1):
        table.add_row(str(i), escape(entry))
    console.print(table)
    return True


def _cmd_tasks(session: ReplSession, console: Console, _args: list[str]) -> bool:
    tasks = session.task_registry.list_recent(n=50)
    if not tasks:
        console.print(f"[{DIM}]no tasks recorded this session.[/]")
        return True

    table = repl_table(title="Tasks", title_style=BOLD_BRAND)
    table.add_column("id", style="bold")
    table.add_column("kind")
    table.add_column("status")
    table.add_column("started", style=DIM)
    table.add_column("duration", style=DIM, justify="right")
    table.add_column("detail", style=DIM, overflow="fold")

    status_style = {
        TaskStatus.RUNNING: WARNING,
        TaskStatus.COMPLETED: HIGHLIGHT,
        TaskStatus.CANCELLED: WARNING,
        TaskStatus.FAILED: ERROR,
        TaskStatus.PENDING: DIM,
    }
    for task in tasks:
        st = status_style.get(task.status, DIM)
        table.add_row(
            task.task_id,
            task.kind.value,
            f"[{st}]{task.status.value}[/]",
            _task_started_label(task),
            _task_duration_label(task),
            escape(_task_detail_label(task)),
        )
    console.print(table)
    return True


def _cmd_stop(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    console.print(
        f"[{DIM}]in-flight work: press[/] [bold]Ctrl+C[/bold] "
        f"[{DIM}]during a streaming investigation, or run[/] [{HIGHLIGHT}]/tasks[/] "
        f"[{DIM}]then[/] [{HIGHLIGHT}]/cancel <id>[/] [{DIM}]for background tasks.[/]"
    )
    return True


def _validate_cancel_args(args: list[str]) -> str | None:
    if not args:
        return f"[{ERROR}]usage:[/] /cancel <task_id>  — use [{HIGHLIGHT}]/tasks[/] to list ids"
    return None


def _cmd_cancel(session: ReplSession, console: Console, args: list[str]) -> bool:
    needle = args[0]
    candidates = session.task_registry.candidates(needle)
    if not candidates:
        console.print(f"[{ERROR}]no task matches id:[/] {escape(needle)}")
        return True
    if len(candidates) > 1:
        console.print(
            f"[{ERROR}]ambiguous id prefix:[/] {escape(needle)} "
            f"[{DIM}]({len(candidates)} matches — use a longer prefix)[/]"
        )
        return True

    task = candidates[0]
    if task.status != TaskStatus.RUNNING:
        console.print(
            f"[{DIM}]task {escape(task.task_id)} already finished (status: {task.status.value}).[/]"
        )
        return True

    task.request_cancel()
    if task.kind == TaskKind.INVESTIGATION:
        console.print(
            f"[{WARNING}]cancellation signaled.[/] "
            f"[{DIM}]if the investigation is still streaming, press[/] [bold]Ctrl+C[/bold] "
            f"[{DIM}]to interrupt the current run.[/]"
        )
    else:
        console.print(
            f"[{HIGHLIGHT}]stop requested[/] "
            f"[{DIM}]for {escape(task.kind.value)} {escape(task.task_id)}.[/] "
            f"[{DIM}]use[/] [{HIGHLIGHT}]/tasks[/] [{DIM}]to confirm status.[/]"
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
        validate_args=_validate_cancel_args,
    ),
    SlashCommand(
        "/stop",
        "hints for stopping in-flight investigations and background tasks",
        _cmd_stop,
    ),
]

__all__ = ["COMMANDS"]
