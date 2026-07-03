"""Shell command runner: execute builtins and record results."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

import config.constants.platform as _platform
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.subprocess_runner.task_streaming import (
    _MAX_COMMAND_OUTPUT_CHARS,
    SHELL_COMMAND_TIMEOUT_SECONDS,
)
from surfaces.interactive_shell.ui import ERROR, HIGHLIGHT, print_command_output
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from surfaces.interactive_shell.utils.error_handling.exception_reporting import report_exception
from tools.interactive_shell.shell import execution as shell_execution
from tools.interactive_shell.shell.display import format_shell_command_for_display
from tools.interactive_shell.shell.parsing import (
    argv_for_repl_builtin_detection,
    parse_shell_command,
)
from tools.interactive_shell.shell.policy import plan_shell_execution


def _shell_payload(
    *,
    command: str,
    ok: bool,
    response_text: str | None = None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    timed_out: bool = False,
    truncated: bool = False,
    executed_with_shell: bool | None = None,
    cancelled: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "command": command,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "truncated": truncated,
        "cancelled": cancelled,
    }
    if executed_with_shell is not None:
        payload["executed_with_shell"] = executed_with_shell
    if response_text:
        payload["response_text"] = response_text.strip()
    return payload


def run_shell_command(
    command: str,
    session: Session,
    console: Console,
    *,
    argv: list[str] | None = None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> dict[str, Any]:
    parsed = parse_shell_command(command, is_windows=_platform.IS_WINDOWS)
    plan = plan_shell_execution(parsed)
    display_command = format_shell_command_for_display(command)
    if not execution_allowed(
        plan.policy,
        session=session,
        console=console,
        action_summary=f"$ {display_command}",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("shell", command, ok=False)
        return _shell_payload(
            command=command,
            ok=False,
            response_text=plan.policy.reason or "shell command blocked",
            cancelled=plan.policy.verdict != "deny",
        )

    console.print(f"[bold]$ {escape(display_command)}[/bold]")

    argv_builtin = argv_for_repl_builtin_detection(parsed=parsed, is_windows=_platform.IS_WINDOWS)

    if argv_builtin is not None and argv_builtin[0].lower() == "cd":
        return run_cd_command(parsed.command, session, console)
    if argv_builtin is not None and argv_builtin[0].lower() == "pwd":
        return run_pwd_command(parsed.command, session, console)

    use_shell = parsed.use_shell
    if parsed.passthrough:
        from surfaces.interactive_shell.ui import DIM

        console.print(f"[{DIM}]explicit shell passthrough enabled[/]")

    exec_argv = argv if argv is not None else parsed.argv

    response_text: str | None = None

    try:
        result = shell_execution.execute_shell_command(
            command=parsed.command,
            argv=exec_argv,
            use_shell=use_shell,
            timeout_seconds=SHELL_COMMAND_TIMEOUT_SECONDS,
            max_output_chars=_MAX_COMMAND_OUTPUT_CHARS,
        )
    except Exception as exc:
        report_exception(exc, context="surfaces.interactive_shell.shell_command.start")

        response_text = f"command failed to start: {str(exc)}"

        console.print(f"[{ERROR}]command failed to start:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(
            command=command,
            ok=False,
            response_text=response_text,
            stderr=str(exc),
            executed_with_shell=use_shell,
        )

    print_command_output(console, result.stdout)
    print_command_output(console, result.stderr, style=ERROR)
    if result.timed_out:
        response_text = f"command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds"

        console.print(
            f"[{ERROR}]command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/]"
        )
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(
            command=command,
            ok=False,
            response_text=response_text,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=True,
            truncated=result.truncated,
            executed_with_shell=result.executed_with_shell,
        )
    ok = result.exit_code == 0
    had_stdout = bool((result.stdout or "").strip())
    had_stderr = bool((result.stderr or "").strip())
    if ok:
        if had_stdout:
            response_text = (result.stdout or "").strip()
        elif had_stderr:
            response_text = (result.stderr or "").strip()
        else:
            console.print(f"[{HIGHLIGHT}]✓[/]")
    else:
        code = result.exit_code if result.exit_code is not None else "?"
        exit_text = f"✗ exit {code}"
        console.print(f"[{ERROR}]✗[/] exit {code}")

        response_parts = []
        if had_stdout:
            response_parts.append((result.stdout or "").strip())
        if had_stderr:
            response_parts.append((result.stderr or "").strip())
        response_parts.append(exit_text)
        response_text = "\n".join(response_parts)

    session.record("shell", command, ok=ok, response_text=response_text)
    stderr_for_result = "" if ok and had_stdout else result.stderr
    return _shell_payload(
        command=command,
        ok=ok,
        response_text=response_text,
        stdout=result.stdout,
        stderr=stderr_for_result,
        exit_code=result.exit_code,
        timed_out=False,
        truncated=result.truncated,
        executed_with_shell=result.executed_with_shell,
    )


def run_cd_command(command: str, session: Session, console: Console) -> dict[str, Any]:
    def _strip_outer_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value

    try:
        tokens = shlex.split(command, posix=not _platform.IS_WINDOWS)
        if _platform.IS_WINDOWS and len(tokens) > 1:
            tokens = [tokens[0], *(_strip_outer_quotes(token) for token in tokens[1:])]
    except ValueError as exc:
        response_text = f"cd failed: {str(exc)}"

        console.print(f"[{ERROR}]cd failed:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    if len(tokens) > 2:
        response_text = "cd failed: too many arguments"

        console.print(f"[{ERROR}]cd failed:[/] too many arguments")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    target = Path(tokens[1]).expanduser() if len(tokens) == 2 else Path.home()
    try:
        os.chdir(target)
    except Exception as exc:
        report_exception(exc, context="surfaces.interactive_shell.shell_cd")

        response_text = f"cd failed: {str(exc)}"

        console.print(f"[{ERROR}]cd failed:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    console.print(Text(str(Path.cwd())))
    session.record("shell", command)
    return _shell_payload(command=command, ok=True, response_text=str(Path.cwd()))


def run_pwd_command(command: str, session: Session, console: Console) -> dict[str, Any]:
    try:
        tokens = shlex.split(command, posix=not _platform.IS_WINDOWS)
    except ValueError as exc:
        response_text = f"pwd failed: {str(exc)}"

        console.print(f"[{ERROR}]pwd failed:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    if len(tokens) != 1:
        response_text = "pwd failed: too many arguments"

        console.print(f"[{ERROR}]pwd failed:[/] too many arguments")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    cwd = str(Path.cwd())
    console.print(Text(cwd))
    session.record("shell", command)
    return _shell_payload(command=command, ok=True, response_text=cwd, stdout=cwd)


__all__ = ["run_cd_command", "run_pwd_command", "run_shell_command"]
