"""Execute planned shell, sample alert, and synthetic test actions."""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

import app.cli.interactive_shell.intent_parser as _intent_parser
from app.cli.interactive_shell.rendering import print_command_output
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.shell_execution import execute_shell_command
from app.cli.interactive_shell.shell_policy import evaluate_policy, parse_shell_command
from app.cli.interactive_shell.tasks import TaskKind, TaskRecord
from app.cli.support.errors import OpenSREError

SHELL_COMMAND_TIMEOUT_SECONDS = 120
SYNTHETIC_TEST_TIMEOUT_SECONDS = 1800
_SYNTHETIC_POLL_SECONDS = 0.25
_MAX_COMMAND_OUTPUT_CHARS = 24_000
_SYNTHETIC_DIAG_CHARS = 2_000  # max stderr bytes captured from a failing synthetic run
_SIGTERM_GRACE_SECONDS = 10  # wait for clean exit after SIGTERM before escalating to SIGKILL


def terminate_child_process(proc: subprocess.Popen[Any]) -> None:
    """Best-effort SIGTERM → wait → SIGKILL → wait without blocking forever."""
    if proc.poll() is not None:
        return
    with contextlib.suppress(OSError):
        proc.terminate()
    try:
        proc.wait(timeout=_SIGTERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


def read_diag(buf: tempfile.SpooledTemporaryFile[bytes]) -> str:  # type: ignore[type-arg]
    """Read up to ``_SYNTHETIC_DIAG_CHARS`` bytes from a captured stderr buffer."""
    buf.seek(0)
    return buf.read(_SYNTHETIC_DIAG_CHARS).decode("utf-8", errors="replace").strip()


def watch_synthetic_subprocess(
    task: TaskRecord,
    proc: subprocess.Popen[Any],
    session: ReplSession,
    suite_name: str,
    stderr_buf: tempfile.SpooledTemporaryFile[bytes],  # type: ignore[type-arg]
) -> None:
    def _history_text() -> str:
        return f"{suite_name} task:{task.task_id}"

    history_gen_when_watch_started = session.history_generation

    def _record_synthetic_if_current_session(ok: bool) -> None:
        if session.history_generation != history_gen_when_watch_started:
            return
        session.record("synthetic_test", _history_text(), ok=ok)

    def _run() -> None:
        started = time.monotonic()
        timed_out = False
        # Track whether *we* explicitly terminated the process so we can
        # distinguish a cancel-driven exit from a natural exit that happened
        # to race with a concurrent /cancel.
        terminated_by_watcher = False
        while proc.poll() is None:
            if time.monotonic() - started > SYNTHETIC_TEST_TIMEOUT_SECONDS:
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
                task.mark_failed(f"timed out after {SYNTHETIC_TEST_TIMEOUT_SECONDS}s")
                _record_synthetic_if_current_session(ok=False)
                return

            code = proc.returncode
            if code is None:
                task.mark_failed("subprocess did not report exit code")
                _record_synthetic_if_current_session(ok=False)
                return

            # Honour the real exit code when the process exited on its own.
            # Only treat as CANCELLED when *we* killed it after a cancel request;
            # a natural exit that races with /cancel should be recorded by its code.
            if terminated_by_watcher and task.cancel_requested.is_set():
                task.mark_cancelled()
                _record_synthetic_if_current_session(ok=False)
                return

            if code == 0:
                task.mark_completed(result="ok")
                _record_synthetic_if_current_session(ok=True)
            else:
                diag = read_diag(stderr_buf)
                error_msg = f"exit code {code}" + (f": {diag}" if diag else "")
                task.mark_failed(error_msg)
                _record_synthetic_if_current_session(ok=False)
        finally:
            stderr_buf.close()

    threading.Thread(target=_run, daemon=True, name=f"synthetic-{task.task_id}").start()


def run_shell_command(command: str, session: ReplSession, console: Console) -> None:
    console.print(f"[bold]$ {escape(command)}[/bold]")
    parsed = parse_shell_command(command, is_windows=_intent_parser.IS_WINDOWS)
    decision = evaluate_policy(parsed=parsed)
    if not decision.allow:
        console.print(
            f"[yellow]command blocked:[/yellow] {escape(decision.reason or 'not allowed')}"
        )
        if decision.hint:
            console.print(f"[dim]{escape(decision.hint)}[/dim]")
        session.record("shell", command, ok=False)
        return

    argv_builtin = parsed.argv
    if argv_builtin is None and parsed.passthrough and parsed.command.strip():
        passthrough_body = parsed.command.strip()
        try:
            argv_builtin = shlex.split(passthrough_body, posix=not _intent_parser.IS_WINDOWS)
        except ValueError:
            try:
                argv_builtin = shlex.split(passthrough_body, posix=False)
            except ValueError:
                argv_builtin = None

    if argv_builtin is not None and argv_builtin[0].lower() == "cd":
        run_cd_command(parsed.command, session, console)
        return
    if argv_builtin is not None and argv_builtin[0].lower() == "pwd":
        run_pwd_command(parsed.command, session, console)
        return

    use_shell = parsed.passthrough
    if use_shell:
        console.print("[dim]explicit shell passthrough enabled[/dim]")

    try:
        result = execute_shell_command(
            command=parsed.command,
            argv=parsed.argv,
            use_shell=use_shell,
            timeout_seconds=SHELL_COMMAND_TIMEOUT_SECONDS,
            max_output_chars=_MAX_COMMAND_OUTPUT_CHARS,
        )
    except subprocess.TimeoutExpired:
        console.print(f"[red]command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/red]")
        session.record("shell", command, ok=False)
        return
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]command failed to start:[/red] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    print_command_output(console, result.stdout)
    print_command_output(console, result.stderr, style="red")
    ok = result.exit_code == 0
    if not ok:
        console.print(f"[red]exit code:[/red] {result.exit_code}")
    elif not result.stdout and not result.stderr:
        console.print("[dim]exit code: 0[/dim]")
    session.record("shell", command, ok=ok)


def run_cd_command(command: str, session: ReplSession, console: Console) -> None:
    def _strip_outer_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value

    try:
        tokens = shlex.split(command, posix=not _intent_parser.IS_WINDOWS)
        if _intent_parser.IS_WINDOWS and len(tokens) > 1:
            tokens = [tokens[0], *(_strip_outer_quotes(token) for token in tokens[1:])]
    except ValueError as exc:
        console.print(f"[red]cd failed:[/red] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    if len(tokens) > 2:
        console.print("[red]cd failed:[/red] too many arguments")
        session.record("shell", command, ok=False)
        return

    target = Path(tokens[1]).expanduser() if len(tokens) == 2 else Path.home()
    try:
        os.chdir(target)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]cd failed:[/red] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    console.print(Text(str(Path.cwd())))
    session.record("shell", command)


def run_pwd_command(command: str, session: ReplSession, console: Console) -> None:
    try:
        tokens = shlex.split(command, posix=not _intent_parser.IS_WINDOWS)
    except ValueError as exc:
        console.print(f"[red]pwd failed:[/red] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    if len(tokens) != 1:
        console.print("[red]pwd failed:[/red] too many arguments")
        session.record("shell", command, ok=False)
        return

    console.print(Text(str(Path.cwd())))
    session.record("shell", command)


def run_sample_alert(template_name: str, session: ReplSession, console: Console) -> None:
    from app.cli.investigation import run_sample_alert_for_session

    console.print(f"[bold]sample alert:[/bold] {escape(template_name)}")
    task = session.task_registry.create(TaskKind.INVESTIGATION)
    task.mark_running()
    try:
        final_state = run_sample_alert_for_session(
            template_name=template_name,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print("[yellow]investigation cancelled.[/yellow]")
        session.record("alert", f"sample:{template_name}", ok=False)
        return
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[red]investigation failed:[/red] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[yellow]suggestion:[/yellow] {escape(exc.suggestion)}")
        session.record("alert", f"sample:{template_name}", ok=False)
        return
    except Exception as exc:  # noqa: BLE001
        task.mark_failed(str(exc))
        console.print(f"[red]investigation failed:[/red] {escape(str(exc))}")
        session.record("alert", f"sample:{template_name}", ok=False)
        return

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.last_state = final_state
    session.accumulate_from_state(final_state)
    session.record("alert", f"sample:{template_name}")


def run_synthetic_test(suite_name: str, session: ReplSession, console: Console) -> None:
    if suite_name != "rds_postgres":
        console.print(f"[red]unknown synthetic suite:[/red] {escape(suite_name)}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    display_command = "opensre tests synthetic"
    console.print(f"[bold]$ {display_command}[/bold]")
    task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
    task.mark_running()
    # Lifetime managed by the watcher thread's finally block. noqa: SIM115
    stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg] # noqa: SIM115
        max_size=_SYNTHETIC_DIAG_CHARS * 2
    )
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is trusted interpreter path + module
            [sys.executable, "-m", "app.cli", "tests", "synthetic"],
            stdout=subprocess.DEVNULL,
            stderr=stderr_buf,
        )
    except Exception as exc:  # noqa: BLE001
        stderr_buf.close()
        task.mark_failed(str(exc))
        console.print(f"[red]synthetic test failed to start:[/red] {escape(str(exc))}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    task.attach_process(proc)
    watch_synthetic_subprocess(task, proc, session, suite_name, stderr_buf)
    console.print(
        f"[dim]synthetic test started — task[/dim] [bold]{escape(task.task_id)}[/bold]. "
        f"[dim]/tasks[/dim] [dim]to monitor,[/dim] [bold]/cancel {escape(task.task_id)}[/bold] "
        f"[dim]to stop.[/dim]"
    )


__all__ = [
    "SHELL_COMMAND_TIMEOUT_SECONDS",
    "SYNTHETIC_TEST_TIMEOUT_SECONDS",
    "read_diag",
    "run_cd_command",
    "run_pwd_command",
    "run_sample_alert",
    "run_shell_command",
    "run_synthetic_test",
    "terminate_child_process",
    "watch_synthetic_subprocess",
]
