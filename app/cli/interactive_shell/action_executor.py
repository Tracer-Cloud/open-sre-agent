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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

import app.cli.interactive_shell.intent_parser as _intent_parser
from app.cli.interactive_shell.execution_policy import (
    evaluate_investigation_launch,
    evaluate_shell_from_parsed,
    evaluate_synthetic_test_launch,
    execution_allowed,
)
from app.cli.interactive_shell.rendering import print_command_output
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.shell_execution import execute_shell_command
from app.cli.interactive_shell.shell_policy import (
    argv_for_repl_builtin_routing,
    parse_shell_command,
)
from app.cli.interactive_shell.tasks import TaskKind, TaskRecord
from app.cli.interactive_shell.theme import TERMINAL_ERROR
from app.cli.support.errors import OpenSREError
from app.cli.support.exception_reporting import report_exception

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


def run_shell_command(
    command: str,
    session: ReplSession,
    console: Console,
    *,
    argv: list[str] | None = None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    parsed = parse_shell_command(command, is_windows=_intent_parser.IS_WINDOWS)
    policy = evaluate_shell_from_parsed(parsed)
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary=f"$ {command}",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("shell", command, ok=False)
        return

    console.print(f"[bold]$ {escape(command)}[/bold]")

    argv_builtin = argv_for_repl_builtin_routing(
        parsed=parsed, is_windows=_intent_parser.IS_WINDOWS
    )

    if argv_builtin is not None and argv_builtin[0].lower() == "cd":
        run_cd_command(parsed.command, session, console)
        return
    if argv_builtin is not None and argv_builtin[0].lower() == "pwd":
        run_pwd_command(parsed.command, session, console)
        return

    use_shell = parsed.passthrough
    if use_shell:
        console.print("[dim]explicit shell passthrough enabled[/dim]")

    exec_argv = argv if argv is not None else parsed.argv

    try:
        result = execute_shell_command(
            command=parsed.command,
            argv=exec_argv,
            use_shell=use_shell,
            timeout_seconds=SHELL_COMMAND_TIMEOUT_SECONDS,
            max_output_chars=_MAX_COMMAND_OUTPUT_CHARS,
        )
    except Exception as exc:
        report_exception(exc, context="interactive_shell.shell_command.start")
        console.print(f"[red]command failed to start:[/red] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    print_command_output(console, result.stdout)
    print_command_output(console, result.stderr, style="red")
    if result.timed_out:
        console.print(f"[red]command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/red]")
        session.record("shell", command, ok=False)
        return
    ok = result.exit_code == 0
    had_stdout = bool((result.stdout or "").strip())
    had_stderr = bool((result.stderr or "").strip())
    if ok:
        if not had_stdout and not had_stderr:
            console.print("[dim]✓[/dim]")
    else:
        code = result.exit_code if result.exit_code is not None else "?"
        console.print(f"[{TERMINAL_ERROR}]✗[/] exit {code}")
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
    except Exception as exc:
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


_OPENSRE_BLOCKED_SUBCOMMANDS: frozenset[str] = frozenset({"agent"})

_READ_ONLY_OPENSRE_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "health",
        "version",
        "list",
        "status",
        "show",
    }
)


def run_opensre_cli_command(args: str, session: ReplSession, console: Console) -> bool:
    """Run an opensre subcommand (not agent).

    Returns True if the command was attempted (regardless of success),
    False if the subcommand is blocked or args are empty.
    """
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    if not tokens:
        return False

    first_token = tokens[0].lower()
    if first_token in _OPENSRE_BLOCKED_SUBCOMMANDS:
        console.print(f"[red]Cannot run `opensre {first_token}`: subcommand is blocked.[/red]")
        return False

    command_classification = (
        "read_only" if first_token in _READ_ONLY_OPENSRE_SUBCOMMANDS else "mutating"
    )
    from app.cli.interactive_shell.execution_policy import ExecutionPolicyResult, execution_allowed

    if command_classification == "read_only":
        policy_result = ExecutionPolicyResult(
            verdict="allow",
            action_type="cli_command",
            reason=None,
            hint=None,
            shell_classification=command_classification,
        )
    else:
        policy_result = ExecutionPolicyResult(
            verdict="ask",
            action_type="cli_command",
            reason="opensre subcommand requires confirmation",
            hint="Use a read-only subcommand (health, version, list, status, show)",
            shell_classification=command_classification,
        )

    if not execution_allowed(
        policy_result,
        session=session,
        console=console,
        action_summary=f"$ opensre {' '.join(tokens)}",
        confirm_fn=None,
        is_tty=None,
        action_already_listed=True,
    ):
        session.record("cli_command", f"opensre {' '.join(tokens)}", ok=False)
        return True

    argv_list = [sys.executable, "-m", "app.cli"] + tokens
    display_command = f"opensre {' '.join(tokens)}"
    console.print(f"[bold]$ {display_command}[/bold]")

    session.record("cli_command", display_command)

    task = session.task_registry.create(TaskKind.CLI_COMMAND)
    task.mark_running()
    stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg] # noqa: SIM115
        max_size=_SYNTHETIC_DIAG_CHARS * 2
    )
    stdout_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg] # noqa: SIM115
        max_size=_MAX_COMMAND_OUTPUT_CHARS
    )
    try:
        proc = subprocess.Popen(
            argv_list,
            stdout=stdout_buf,
            stderr=stderr_buf,
        )
    except Exception as exc:  # noqa: BLE001
        stdout_buf.close()
        stderr_buf.close()
        task.mark_failed(str(exc))
        console.print(f"[red]failed to start:[/red] {escape(str(exc))}")
        return True

    task.attach_process(proc)
    started_at = time.monotonic()

    def _watch() -> None:
        terminated_by_watcher = False
        timed_out = False
        while proc.poll() is None:
            if time.monotonic() - started_at > SHELL_COMMAND_TIMEOUT_SECONDS:
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
                task.mark_failed(f"timed out after {SHELL_COMMAND_TIMEOUT_SECONDS}s")
                stdout_buf.close()
                stderr_buf.close()
                return
            if terminated_by_watcher and task.cancel_requested.is_set():
                task.mark_cancelled()
                stdout_buf.close()
                stderr_buf.close()
                return

            code = proc.returncode
            stdout_buf.seek(0)
            stdout_lines = stdout_buf.read(_MAX_COMMAND_OUTPUT_CHARS).decode(
                "utf-8", errors="replace"
            )
            if code == 0:
                task.mark_completed()
                if stdout_lines:
                    print_command_output(console, stdout_lines)
            else:
                diag = read_diag(stderr_buf)
                error_msg = f"exit code {code}" + (f": {diag}" if diag else "")
                task.mark_failed(error_msg)
                console.print(f"[red]command failed (exit {code}):[/red]")
                if stdout_lines:
                    print_command_output(console, stdout_lines)
                if diag:
                    console.print(f"[dim]{escape(diag)}[/dim]")
        except Exception as exc:  # noqa: BLE001
            task.mark_failed(str(exc))
            console.print(f"[red]error:[/red] {escape(str(exc))}")
        finally:
            stdout_buf.close()
            stderr_buf.close()

    thread = threading.Thread(target=_watch, daemon=True)
    thread.start()
    console.print("[dim]started.[/dim]")
    return True


def run_sample_alert(
    template_name: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    from app.cli.investigation import run_sample_alert_for_session

    policy = evaluate_investigation_launch(action_type="sample_alert")
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary=f"sample alert investigation ({template_name})",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("alert", f"sample:{template_name}", ok=False)
        return

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
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.sample_alert")
        console.print(f"[red]investigation failed:[/red] {escape(str(exc))}")
        session.record("alert", f"sample:{template_name}", ok=False)
        return

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.last_state = final_state
    session.accumulate_from_state(final_state)
    session.record("alert", f"sample:{template_name}")


def run_synthetic_test(
    suite_name: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    if suite_name != "rds_postgres":
        console.print(f"[red]unknown synthetic suite:[/red] {escape(suite_name)}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    policy = evaluate_synthetic_test_launch()
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary="opensre tests synthetic",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("synthetic_test", suite_name, ok=False)
        return

    display_command = "opensre tests synthetic"
    console.print(f"[bold]$ {display_command}[/bold]")
    task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
    task.mark_running()
    # Lifetime managed by the watcher thread's finally block; SIM115 ignored
    # for this file in ruff.toml.
    stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg]
        max_size=_SYNTHETIC_DIAG_CHARS * 2
    )
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.cli", "tests", "synthetic"],
            stdout=subprocess.DEVNULL,
            stderr=stderr_buf,
        )
    except Exception as exc:
        stderr_buf.close()
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.synthetic_test.start")
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
    "run_opensre_cli_command",
    "run_pwd_command",
    "run_sample_alert",
    "run_shell_command",
    "run_synthetic_test",
    "terminate_child_process",
    "watch_synthetic_subprocess",
]
