"""Background CLI task launcher — runs subprocesses with streamed output above the prompt."""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import threading
import time
from typing import Any

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.runtime import ReplSession, TaskKind, TaskRecord
from app.cli.interactive_shell.ui import DIM, ERROR, HIGHLIGHT
from app.cli.support.exception_reporting import report_exception

from .task_streaming import (
    _MAX_COMMAND_OUTPUT_CHARS,
    _SYNTHETIC_DIAG_CHARS,
    _SYNTHETIC_POLL_SECONDS,
    SHELL_COMMAND_TIMEOUT_SECONDS,
    _ae_resolve,
    _join_task_output_streams,
    _pump_task_pty,
    _should_use_pty,
    _start_task_output_streams,
    _subprocess_env_with_aligned_width,
    read_diag,
    terminate_child_process,
)


def start_background_cli_task(
    *,
    display_command: str,
    argv_list: list[str],
    session: ReplSession,
    console: Console,
    timeout_seconds: int = SHELL_COMMAND_TIMEOUT_SECONDS,
    kind: TaskKind = TaskKind.CLI_COMMAND,
    use_pty: bool = False,
) -> TaskRecord | None:
    """Start a subprocess as a REPL task while streaming output above the prompt."""
    console.print(f"[bold]$ {display_command}[/bold]")
    task = session.task_registry.create(kind, command=display_command)
    task.mark_running()
    stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg] # noqa: SIM115
        max_size=_SYNTHETIC_DIAG_CHARS * 2
    )
    pty_fds: tuple[int, int] | None = None
    if _should_use_pty(console, use_pty):
        try:
            pty_fds = os.openpty()
        except OSError:
            pty_fds = None
    stdout_buf: tempfile.SpooledTemporaryFile[bytes] | None = None  # type: ignore[type-arg]
    if pty_fds is None:
        stdout_buf = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg] # noqa: SIM115
            max_size=_MAX_COMMAND_OUTPUT_CHARS
        )
    subprocess_env = _subprocess_env_with_aligned_width(console)
    proc: subprocess.Popen[Any]
    try:
        if pty_fds is None:
            proc = subprocess.Popen(
                argv_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
                env=subprocess_env,
            )
        else:
            _master_fd, slave_fd = pty_fds
            proc = subprocess.Popen(
                argv_list,
                stdin=subprocess.DEVNULL,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
                env=subprocess_env,
            )
    except Exception as exc:  # noqa: BLE001
        if pty_fds is not None:
            for fd in pty_fds:
                with contextlib.suppress(OSError):
                    os.close(fd)
        if stdout_buf is not None:
            stdout_buf.close()
        stderr_buf.close()
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.background_cli_task.start")
        console.print(f"[{ERROR}]failed to start:[/] {escape(str(exc))}")
        return None

    task.attach_process(proc)
    started_at = time.monotonic()
    if pty_fds is None:
        output_threads = _start_task_output_streams(
            task=task,
            proc=proc,
            console=console,
            stdout_capture=stdout_buf,
            stderr_capture=stderr_buf,
        )
    else:
        master_fd, slave_fd = pty_fds
        with contextlib.suppress(OSError):
            os.close(slave_fd)
        output_thread = threading.Thread(
            target=_pump_task_pty,
            kwargs={"master_fd": master_fd, "console": console, "capture": stderr_buf},
            daemon=True,
            name=f"task-terminal-{task.task_id}",
        )
        output_thread.start()
        output_threads = [output_thread]

    def _watch() -> None:
        terminated_by_watcher = False
        timed_out = False
        while proc.poll() is None:
            if time.monotonic() - started_at > timeout_seconds:
                timed_out = True
                task.request_cancel()
                terminate_child_process(proc)
                terminated_by_watcher = True
                break
            if task.cancel_requested.is_set():
                terminate_child_process(proc)
                terminated_by_watcher = True
                break
            time.sleep(_SYNTHETIC_POLL_SECONDS)

        try:
            if timed_out:
                task.mark_failed(f"timed out after {timeout_seconds}s")
                return
            if terminated_by_watcher and task.cancel_requested.is_set():
                task.mark_cancelled()
                return

            _join_task_output_streams(output_threads)
            code = proc.returncode
            if code == 0:
                task.mark_completed()
            else:
                diag = _ae_resolve("read_diag", read_diag)(stderr_buf)
                error_msg = f"exit code {code}" + (f": {diag}" if diag else "")
                task.mark_failed(error_msg)
                console.print(f"[{ERROR}]command failed (exit {code}):[/]")
        except Exception as exc:  # noqa: BLE001
            task.mark_failed(str(exc))
            report_exception(exc, context="interactive_shell.background_cli_task.watch")
            console.print(f"[{ERROR}]error:[/] {escape(str(exc))}")
        finally:
            _join_task_output_streams(output_threads)
            if stdout_buf is not None:
                stdout_buf.close()
            stderr_buf.close()

    thread = threading.Thread(target=_watch, daemon=True)
    thread.start()
    console.print(
        f"[{DIM}]started — task[/] [bold]{escape(task.task_id)}[/bold]. "
        f"[{HIGHLIGHT}]/tasks[/] [{DIM}]to monitor,[/] "
        f"[{HIGHLIGHT}]/cancel {escape(task.task_id)}[/] [{DIM}]to stop.[/]"
    )
    return task
