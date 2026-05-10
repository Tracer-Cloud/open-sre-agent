"""Execute planned shell, sample alert, and synthetic test actions."""

from __future__ import annotations

import contextlib
import errno
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

import app.cli.interactive_shell.intent_parser as _intent_parser
from app.cli.interactive_shell.execution_policy import (
    evaluate_code_agent_launch,
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
from app.cli.interactive_shell.theme import DIM, ERROR, HIGHLIGHT, WARNING
from app.cli.support.errors import OpenSREError
from app.cli.support.exception_reporting import report_exception
from app.integrations.llm_cli.claude_code import ClaudeCodeAdapter
from app.integrations.llm_cli.subprocess_env import build_cli_subprocess_env

SHELL_COMMAND_TIMEOUT_SECONDS = 120
SYNTHETIC_TEST_TIMEOUT_SECONDS = 1800
CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS = 1800
_SYNTHETIC_POLL_SECONDS = 0.25
_MAX_COMMAND_OUTPUT_CHARS = 24_000
_SYNTHETIC_DIAG_CHARS = 2_000  # max stderr bytes captured from a failing synthetic run
_SIGTERM_GRACE_SECONDS = 10  # wait for clean exit after SIGTERM before escalating to SIGKILL
_TASK_OUTPUT_JOIN_TIMEOUT_SECONDS = 2
_IMPLEMENT_PERMISSION_MODE_ENV = "CLAUDE_CODE_IMPLEMENT_PERMISSION_MODE"
_DEFAULT_IMPLEMENT_PERMISSION_MODE = "acceptEdits"


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


def _print_task_output_line(
    console: Console,
    task: TaskRecord,
    stream_name: str,
    line: str,
    *,
    style: str | None = None,
) -> None:
    text = Text()
    text.append(f"{task.task_id} {stream_name} │ ", style=DIM)
    text.append(line.rstrip("\r\n"), style=style)
    console.print(text)


def _pump_task_stream(
    *,
    task: TaskRecord,
    stream_name: str,
    stream: IO[str],
    console: Console,
    style: str | None = None,
    capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
) -> None:
    try:
        for line in stream:
            if capture is not None:
                capture.write(line.encode("utf-8", errors="replace"))
            if line.strip():
                _print_task_output_line(console, task, stream_name, line, style=style)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[{DIM}]task output stream ended unexpectedly:[/] {escape(str(exc))}")


def _start_task_output_streams(
    *,
    task: TaskRecord,
    proc: subprocess.Popen[Any],
    console: Console,
    stdout_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
    stderr_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
) -> list[threading.Thread]:
    threads: list[threading.Thread] = []
    streams: tuple[tuple[str, IO[str] | None, str | None, Any], ...] = (
        ("stdout", proc.stdout, None, stdout_capture),
        ("stderr", proc.stderr, ERROR, stderr_capture),
    )
    for stream_name, stream, style, capture in streams:
        if stream is None:
            continue
        thread = threading.Thread(
            target=_pump_task_stream,
            kwargs={
                "task": task,
                "stream_name": stream_name,
                "stream": stream,
                "console": console,
                "style": style,
                "capture": capture,
            },
            daemon=True,
            name=f"task-output-{task.task_id}-{stream_name}",
        )
        thread.start()
        threads.append(thread)
    return threads


def _join_task_output_streams(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.join(timeout=_TASK_OUTPUT_JOIN_TIMEOUT_SECONDS)


def _console_file_is_tty(console: Console) -> bool:
    isatty = getattr(console.file, "isatty", None)
    return bool(isatty and isatty())


def _should_use_pty(console: Console, requested: bool) -> bool:
    return requested and hasattr(os, "openpty") and _console_file_is_tty(console)


def _pump_task_pty(
    *,
    master_fd: int,
    console: Console,
    capture: tempfile.SpooledTemporaryFile[bytes],  # type: ignore[type-arg]
) -> None:
    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError as exc:
                # BSD/macOS PTYs raise EIO at EOF; Linux commonly returns b"".
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            capture.write(chunk)
            console.file.write(chunk.decode("utf-8", errors="replace"))
            console.file.flush()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[{DIM}]task terminal stream ended unexpectedly:[/] {escape(str(exc))}")
    finally:
        with contextlib.suppress(OSError):
            os.close(master_fd)


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
                diag = read_diag(stderr_buf)
                error_msg = f"exit code {code}" + (f": {diag}" if diag else "")
                task.mark_failed(error_msg)
                console.print(f"[{ERROR}]command failed (exit {code}):[/]")
        except Exception as exc:  # noqa: BLE001
            task.mark_failed(str(exc))
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


def watch_synthetic_subprocess(
    task: TaskRecord,
    proc: subprocess.Popen[Any],
    session: ReplSession,
    suite_name: str,
    stderr_buf: tempfile.SpooledTemporaryFile[bytes],  # type: ignore[type-arg]
    console: Console | None = None,
) -> None:
    def _history_text() -> str:
        return f"{suite_name} task:{task.task_id}"

    history_gen_when_watch_started = session.history_generation

    def _record_synthetic_if_current_session(ok: bool) -> None:
        if session.history_generation != history_gen_when_watch_started:
            return
        session.record("synthetic_test", _history_text(), ok=ok)

    def _run() -> None:
        output_threads = (
            _start_task_output_streams(
                task=task,
                proc=proc,
                console=console,
                stderr_capture=stderr_buf,
            )
            if console is not None
            else []
        )
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

            _join_task_output_streams(output_threads)
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
            _join_task_output_streams(output_threads)
            stderr_buf.close()

    threading.Thread(target=_run, daemon=True, name=f"synthetic-{task.task_id}").start()


def _recent_cli_agent_context(session: ReplSession, *, limit: int = 6) -> str:
    recent = session.cli_agent_messages[-limit:]
    if not recent:
        return ""
    return "\n".join(f"{role}: {text}" for role, text in recent)


def _is_context_dependent_implementation_request(request: str) -> bool:
    normalized = " ".join(request.strip().lower().split())
    return normalized in {
        "implement",
        "please implement",
        "code",
        "make the change",
        "make those changes",
    }


def _build_claude_code_implementation_prompt(request: str, session: ReplSession) -> str:
    context = _recent_cli_agent_context(session)
    context_block = (
        f"--- Recent OpenSRE terminal assistant context ---\n{context}\n\n" if context else ""
    )
    return (
        "You are Claude Code working in the current OpenSRE repository.\n\n"
        f"{context_block}"
        f"--- User implementation request ---\n{request.strip()}\n\n"
        "--- Rules ---\n"
        "- Implement the requested change in this repository.\n"
        "- Follow AGENTS.md, existing project conventions, and local code style.\n"
        "- Do not create a git commit or push changes.\n"
        "- Do not run destructive git commands such as reset --hard or checkout --.\n"
        "- Preserve unrelated user changes in the working tree.\n"
        "- Run focused tests or lint checks when practical.\n"
        "- Finish with a concise summary of changed files and verification performed.\n"
    )


def _implementation_argv(argv: tuple[str, ...]) -> list[str]:
    exec_argv = list(argv)
    permission_mode = os.environ.get(
        _IMPLEMENT_PERMISSION_MODE_ENV,
        _DEFAULT_IMPLEMENT_PERMISSION_MODE,
    ).strip()
    if permission_mode and permission_mode.lower() not in {"default", "none", "off"}:
        exec_argv.extend(["--permission-mode", permission_mode])
    return exec_argv


def run_claude_code_implementation(
    request: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    policy = evaluate_code_agent_launch()
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary=f"Claude Code implementation: {request}",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("implementation", request, ok=False)
        return

    if _is_context_dependent_implementation_request(request) and not session.cli_agent_messages:
        console.print(
            f"[{ERROR}]implementation request is too vague:[/] "
            "describe what Claude Code should change."
        )
        session.record("implementation", request, ok=False)
        return

    adapter = ClaudeCodeAdapter()
    probe = adapter.detect()
    if not probe.installed or not probe.bin_path:
        console.print(f"[{ERROR}]Claude Code CLI not available:[/] {escape(probe.detail)}")
        session.record("implementation", request, ok=False)
        return
    if probe.logged_in is False:
        console.print(f"[{ERROR}]Claude Code is not authenticated:[/] {escape(probe.detail)}")
        session.record("implementation", request, ok=False)
        return

    prompt = _build_claude_code_implementation_prompt(request, session)
    try:
        invocation = adapter.build(
            prompt=prompt,
            model=os.environ.get("CLAUDE_CODE_MODEL"),
            workspace=str(Path.cwd()),
        )
    except Exception as exc:
        report_exception(exc, context="interactive_shell.claude_code.build")
        console.print(f"[{ERROR}]Claude Code failed to prepare:[/] {escape(str(exc))}")
        session.record("implementation", request, ok=False)
        return

    display_command = "claude -p"
    console.print(f"[bold]$ {display_command}[/bold]")
    task = session.task_registry.create(TaskKind.CODE_AGENT, command=display_command)
    task.mark_running()
    history_gen_when_started = session.history_generation

    try:
        proc = subprocess.Popen(
            _implementation_argv(invocation.argv),
            stdin=subprocess.PIPE if invocation.stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=invocation.cwd,
            env=build_cli_subprocess_env(invocation.env),
            start_new_session=True,
        )
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.claude_code.start")
        console.print(f"[{ERROR}]Claude Code failed to start:[/] {escape(str(exc))}")
        session.record("implementation", request, ok=False)
        return

    task.attach_process(proc)
    session.record("implementation", request, ok=True)

    def _watch() -> None:
        try:
            timed_out = False
            try:
                stdout, stderr = proc.communicate(
                    input=invocation.stdin,
                    timeout=CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                timed_out = True
                task.request_cancel()
                terminate_child_process(proc)
                stdout, stderr = proc.communicate()

            out = (stdout or "")[:_MAX_COMMAND_OUTPUT_CHARS]
            err = (stderr or "")[:_MAX_COMMAND_OUTPUT_CHARS]
            if timed_out:
                task.mark_failed(f"timed out after {CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS}s")
                console.print(
                    f"[{ERROR}]Claude Code timed out after "
                    f"{CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS} seconds[/]"
                )
                return

            code = proc.returncode
            if task.cancel_requested.is_set() and code != 0:
                task.mark_cancelled()
                if session.history_generation == history_gen_when_started:
                    session.mark_latest(ok=False, kind="implementation")
                console.print(f"[{WARNING}]Claude Code task cancelled.[/]")
                return

            if code == 0:
                task.mark_completed(result="ok")
                console.print(f"[{HIGHLIGHT}]Claude Code completed[/] task {task.task_id}")
                print_command_output(console, out)
                if err:
                    print_command_output(console, err, style=DIM)
                return

            diag = (err or out).strip()[:_SYNTHETIC_DIAG_CHARS]
            error_msg = f"exit code {code}" + (f": {diag}" if diag else "")
            task.mark_failed(error_msg)
            if session.history_generation == history_gen_when_started:
                session.mark_latest(ok=False, kind="implementation")
            console.print(f"[{ERROR}]Claude Code failed (exit {code}):[/]")
            print_command_output(console, out)
            print_command_output(console, err, style=ERROR)
        except Exception as exc:  # noqa: BLE001
            task.mark_failed(str(exc))
            report_exception(exc, context="interactive_shell.claude_code.watch")
            if session.history_generation == history_gen_when_started:
                session.mark_latest(ok=False, kind="implementation")
            console.print(f"[{ERROR}]Claude Code watcher failed:[/] {escape(str(exc))}")

    threading.Thread(target=_watch, daemon=True, name=f"claude-code-{task.task_id}").start()
    console.print(
        f"[{DIM}]Claude Code started — task[/] [bold]{escape(task.task_id)}[/bold]. "
        f"[{HIGHLIGHT}]/tasks[/] [{DIM}]to monitor,[/] "
        f"[{HIGHLIGHT}]/cancel {escape(task.task_id)}[/] [{DIM}]to stop.[/]"
    )


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
        console.print(f"[{DIM}]explicit shell passthrough enabled[/]")

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
        console.print(f"[{ERROR}]command failed to start:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    print_command_output(console, result.stdout)
    print_command_output(console, result.stderr, style=ERROR)
    if result.timed_out:
        console.print(
            f"[{ERROR}]command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/]"
        )
        session.record("shell", command, ok=False)
        return
    ok = result.exit_code == 0
    had_stdout = bool((result.stdout or "").strip())
    had_stderr = bool((result.stderr or "").strip())
    if ok:
        if not had_stdout and not had_stderr:
            console.print(f"[{HIGHLIGHT}]✓[/]")
    else:
        code = result.exit_code if result.exit_code is not None else "?"
        console.print(f"[{ERROR}]✗[/] exit {code}")
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
        console.print(f"[{ERROR}]cd failed:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    if len(tokens) > 2:
        console.print(f"[{ERROR}]cd failed:[/] too many arguments")
        session.record("shell", command, ok=False)
        return

    target = Path(tokens[1]).expanduser() if len(tokens) == 2 else Path.home()
    try:
        os.chdir(target)
    except Exception as exc:
        console.print(f"[{ERROR}]cd failed:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    console.print(Text(str(Path.cwd())))
    session.record("shell", command)


def run_pwd_command(command: str, session: ReplSession, console: Console) -> None:
    try:
        tokens = shlex.split(command, posix=not _intent_parser.IS_WINDOWS)
    except ValueError as exc:
        console.print(f"[{ERROR}]pwd failed:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False)
        return

    if len(tokens) != 1:
        console.print(f"[{ERROR}]pwd failed:[/] too many arguments")
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


def _classify_opensre_command(tokens: list[str]) -> str:
    first_token = tokens[0].lower()
    if first_token in _READ_ONLY_OPENSRE_SUBCOMMANDS:
        return "read_only"
    if first_token == "agents":
        subcommand = tokens[1].lower() if len(tokens) > 1 else "list"
        if subcommand in {"list"}:
            return "read_only"
        if subcommand == "scan" and "--register" not in tokens[2:]:
            return "read_only"
    return "mutating"


def _opensre_confirmation_reason(tokens: list[str]) -> str:
    if tokens[:2] == ["agents", "scan"] and "--register" in tokens[2:]:
        return "register discovered local AI-agent processes"
    if tokens and tokens[0] == "agents":
        return "this updates the local AI-agent registry"
    return "this opensre subcommand may change local config or infrastructure"


def _should_run_opensre_in_foreground(tokens: list[str]) -> bool:
    first_token = tokens[0].lower()
    if first_token in _READ_ONLY_OPENSRE_SUBCOMMANDS:
        return True
    if first_token == "agents":
        subcommand = tokens[1].lower() if len(tokens) > 1 else "list"
        return subcommand in {"list", "register", "forget", "scan", "watch"}
    return False


def _run_opensre_foreground(
    argv_list: list[str],
    display_command: str,
    session: ReplSession,
    console: Console,
) -> None:
    console.print(f"[bold]$ {escape(display_command)}[/bold]")
    try:
        result = subprocess.run(
            argv_list,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        print_command_output(console, str(exc.output or ""))
        print_command_output(console, str(exc.stderr or ""), style=ERROR)
        console.print(
            f"[{ERROR}]command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/]"
        )
        session.record("cli_command", display_command, ok=False)
        return
    except Exception as exc:  # noqa: BLE001
        report_exception(exc, context="interactive_shell.opensre_cli.start")
        console.print(f"[{ERROR}]failed to start:[/] {escape(str(exc))}")
        session.record("cli_command", display_command, ok=False)
        return

    print_command_output(console, result.stdout)
    print_command_output(console, result.stderr, style=ERROR)
    ok = result.returncode == 0
    if not ok:
        console.print(f"[{ERROR}]command failed (exit {result.returncode}):[/]")
    session.record("cli_command", display_command, ok=ok)


def _run_opensre_foreground_streaming(
    argv_list: list[str],
    display_command: str,
    session: ReplSession,
    console: Console,
) -> None:
    console.print(f"[bold]$ {escape(display_command)}[/bold]")
    try:
        proc = subprocess.Popen(
            argv_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:  # noqa: BLE001
        report_exception(exc, context="interactive_shell.opensre_cli.start")
        console.print(f"[{ERROR}]failed to start:[/] {escape(str(exc))}")
        session.record("cli_command", display_command, ok=False)
        return

    if proc.stdout is not None:
        for line in proc.stdout:
            print_command_output(console, line)
    code = proc.wait()
    ok = code == 0
    if not ok:
        console.print(f"[{ERROR}]command failed (exit {code}):[/]")
    session.record("cli_command", display_command, ok=ok)


def run_opensre_cli_command(
    args: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
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
        console.print(f"[{ERROR}]Cannot run `opensre {first_token}`: subcommand is blocked.[/]")
        return False

    command_classification = _classify_opensre_command(tokens)
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
            reason=_opensre_confirmation_reason([token.lower() for token in tokens]),
            hint="Use a read-only subcommand (health, version, list, status, show)",
            shell_classification=command_classification,
        )

    if not execution_allowed(
        policy_result,
        session=session,
        console=console,
        action_summary=f"$ opensre {' '.join(tokens)}",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=True,
    ):
        session.record("cli_command", f"opensre {' '.join(tokens)}", ok=False)
        return True

    argv_list = [sys.executable, "-m", "app.cli"] + tokens
    display_command = f"opensre {' '.join(tokens)}"
    if _should_run_opensre_in_foreground(tokens):
        if [token.lower() for token in tokens[:2]] == ["agents", "watch"]:
            _run_opensre_foreground_streaming(argv_list, display_command, session, console)
            return True
        _run_opensre_foreground(argv_list, display_command, session, console)
        return True

    session.record("cli_command", display_command)
    start_background_cli_task(
        display_command=display_command,
        argv_list=argv_list,
        session=session,
        console=console,
    )
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
    task = session.task_registry.create(
        TaskKind.INVESTIGATION, command=f"sample alert:{template_name}"
    )
    task.mark_running()
    try:
        final_state = run_sample_alert_for_session(
            template_name=template_name,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        session.record("alert", f"sample:{template_name}", ok=False)
        return
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
        session.record("alert", f"sample:{template_name}", ok=False)
        return
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.sample_alert")
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
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
        console.print(f"[{ERROR}]unknown synthetic suite:[/] {escape(suite_name)}")
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
    task = session.task_registry.create(TaskKind.SYNTHETIC_TEST, command=display_command)
    task.mark_running()
    # Lifetime managed by the watcher thread's finally block; SIM115 ignored
    # for this file in ruff.toml.
    stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg]
        max_size=_SYNTHETIC_DIAG_CHARS * 2
    )
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.cli", "tests", "synthetic"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except Exception as exc:
        stderr_buf.close()
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.synthetic_test.start")
        console.print(f"[{ERROR}]synthetic test failed to start:[/] {escape(str(exc))}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    task.attach_process(proc)
    watch_synthetic_subprocess(task, proc, session, suite_name, stderr_buf, console)
    console.print(
        f"[{DIM}]synthetic test started — task[/] [bold]{escape(task.task_id)}[/bold]. "
        f"[{HIGHLIGHT}]/tasks[/] [{DIM}]to monitor,[/] "
        f"[{HIGHLIGHT}]/cancel {escape(task.task_id)}[/] [{DIM}]to stop.[/]"
    )


__all__ = [
    "SHELL_COMMAND_TIMEOUT_SECONDS",
    "SYNTHETIC_TEST_TIMEOUT_SECONDS",
    "read_diag",
    "run_claude_code_implementation",
    "run_cd_command",
    "run_opensre_cli_command",
    "run_pwd_command",
    "run_sample_alert",
    "run_shell_command",
    "run_synthetic_test",
    "start_background_cli_task",
    "terminate_child_process",
    "watch_synthetic_subprocess",
]
