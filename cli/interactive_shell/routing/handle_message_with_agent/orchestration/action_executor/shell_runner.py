"""Shell command runner — execute, route builtins, and record results."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.text import Text

import cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser as _intent_parser
from cli.interactive_shell.error_handling.exception_reporting import report_exception
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    execution_allowed,
    plan_shell_execution,
)
from cli.interactive_shell.runtime import ReplSession
from cli.interactive_shell.shell import (
    argv_for_repl_builtin_routing,
    execute_shell_command,
    parse_shell_command,
)
from cli.interactive_shell.ui import ERROR, HIGHLIGHT, print_command_output

from .task_streaming import (
    _MAX_COMMAND_OUTPUT_CHARS,
    SHELL_COMMAND_TIMEOUT_SECONDS,
    _ae_resolve,
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
    plan = plan_shell_execution(parsed)
    if not execution_allowed(
        plan.policy,
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
        from cli.interactive_shell.ui import DIM

        console.print(f"[{DIM}]explicit shell passthrough enabled[/]")

    exec_argv = argv if argv is not None else parsed.argv

    response_text: str | None = None

    try:
        result = _ae_resolve("execute_shell_command", execute_shell_command)(
            command=parsed.command,
            argv=exec_argv,
            use_shell=use_shell,
            timeout_seconds=SHELL_COMMAND_TIMEOUT_SECONDS,
            max_output_chars=_MAX_COMMAND_OUTPUT_CHARS,
        )
    except Exception as exc:
        report_exception(exc, context="interactive_shell.shell_command.start")

        response_text = f"command failed to start: {str(exc)}"

        console.print(f"[{ERROR}]command failed to start:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False, response_text=response_text)
        return

    print_command_output(console, result.stdout)
    print_command_output(console, result.stderr, style=ERROR)
    if result.timed_out:
        response_text = f"command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds"

        console.print(
            f"[{ERROR}]command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/]"
        )
        session.record("shell", command, ok=False, response_text=response_text)
        return
    ok = result.exit_code == 0
    had_stdout = bool((result.stdout or "").strip())
    had_stderr = bool((result.stderr or "").strip())
    if ok:
        if not had_stdout and not had_stderr:
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
        response_text = f"cd failed: {str(exc)}"

        console.print(f"[{ERROR}]cd failed:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False, response_text=response_text)
        return

    if len(tokens) > 2:
        response_text = "cd failed: too many arguments"

        console.print(f"[{ERROR}]cd failed:[/] too many arguments")
        session.record("shell", command, ok=False, response_text=response_text)
        return

    target = Path(tokens[1]).expanduser() if len(tokens) == 2 else Path.home()
    try:
        os.chdir(target)
    except Exception as exc:
        report_exception(exc, context="interactive_shell.shell_cd")

        response_text = f"cd failed: {str(exc)}"

        console.print(f"[{ERROR}]cd failed:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False, response_text=response_text)
        return

    console.print(Text(str(Path.cwd())))
    session.record("shell", command)


def run_pwd_command(command: str, session: ReplSession, console: Console) -> None:
    try:
        tokens = shlex.split(command, posix=not _intent_parser.IS_WINDOWS)
    except ValueError as exc:
        response_text = f"pwd failed: {str(exc)}"

        console.print(f"[{ERROR}]pwd failed:[/] {escape(str(exc))}")
        session.record("shell", command, ok=False, response_text=response_text)
        return

    if len(tokens) != 1:
        response_text = "pwd failed: too many arguments"

        console.print(f"[{ERROR}]pwd failed:[/] too many arguments")
        session.record("shell", command, ok=False, response_text=response_text)
        return

    console.print(Text(str(Path.cwd())))
    session.record("shell", command)
