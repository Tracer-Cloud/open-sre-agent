"""Shared foreground investigation task lifecycle for REPL entry points."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.error_handling.errors import OpenSREError
from app.cli.interactive_shell.error_handling.exception_reporting import report_exception
from app.cli.interactive_shell.runtime import ReplSession, TaskKind
from app.cli.interactive_shell.runtime.tasks import TaskRecord
from app.cli.interactive_shell.ui import ERROR, WARNING


def run_foreground_investigation(
    *,
    session: ReplSession,
    console: Console,
    task_command: str,
    run: Callable[[TaskRecord], dict[str, Any]],
    exception_context: str,
) -> dict[str, Any] | None:
    """Run one foreground investigation with shared task and error handling.

    Returns the investigation final state on success, or ``None`` when the run
    was cancelled or failed.
    """
    task = session.task_registry.create(TaskKind.INVESTIGATION, command=task_command)
    task.mark_running()
    try:
        final_state = run(task)
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        return None
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
        return None
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context=exception_context)
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        return None

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.apply_investigation_result(final_state)
    return final_state


__all__ = ["run_foreground_investigation"]
