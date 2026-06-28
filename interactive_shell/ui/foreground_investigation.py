"""Shared foreground investigation task lifecycle for REPL entry points."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape

from interactive_shell.utils.error_handling.exception_reporting import report_exception
from platform.common.errors import OpenSREError
from platform.common.task_types import TaskKind, TaskRecord
from platform.terminal.theme import ERROR, WARNING

if TYPE_CHECKING:
    from interactive_shell.session import ReplSession


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
    session.apply_investigation_result(final_state, trigger=task_command)

    # Mirror the standalone CLI (run_investigation_cli_streaming): show the
    # blocking RCA-accuracy feedback menu after the report. Pass console=None so
    # the cursor-safe _run_select (per-line erase) is used instead of
    # repl_choose_one, whose block-erase is unstable after Rich Live streaming.
    #
    # Safety check: only read stdin when prompt_async is NOT running.
    # When the investigation was dispatched without exclusive stdin (e.g. an
    # LLM-agent free-text message), prompt_async is already active and its
    # Application periodically sends ESC[6n CPR queries. Those terminal responses
    # (ESC[row;colR) arrive in stdin while read_key_unix is blocking. Even with the
    # CSI-drain fix in read_key_unix, racing with an active Application is unsafe:
    # skip the feedback menu and avoid the stdin conflict entirely.
    from interactive_shell.ui.components.key_reader import restore_stdin_terminal
    from interactive_shell.ui.feedback import prompt_investigation_feedback

    pt_app = getattr(session, "pt_style_app", None)
    pt_app_running = pt_app is not None and getattr(pt_app, "is_running", False)
    if not pt_app_running:
        # The explicit pre-call (kept identical to the CLI path) primes the terminal
        # out of the streaming watcher's no-echo/raw mode *before* the feedback
        # helper prints its root-cause context and header.
        restore_stdin_terminal()
        prompt_investigation_feedback(final_state)
    return final_state


__all__ = ["run_foreground_investigation"]
