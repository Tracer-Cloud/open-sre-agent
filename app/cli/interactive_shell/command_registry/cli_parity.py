"""Slash commands for CLI parity, delegating to the Click CLI via subprocess."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.action_executor import (
    SYNTHETIC_TEST_TIMEOUT_SECONDS,
    start_background_cli_task,
)
from app.cli.interactive_shell.command_registry.suggestions import closest_choice
from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.tasks import TaskKind
from app.cli.interactive_shell.theme import DIM, ERROR

_UPDATE_SUBPROCESS_TIMEOUT_SECONDS = 300
_BACKGROUND_TEST_SUBCOMMANDS = frozenset({"run", "synthetic", "cloudopsbench"})
_TEST_SUBCOMMANDS = ("list", "run", "synthetic", "cloudopsbench")
_TEST_PICKER_SELECTION_FILE_ENV = "OPENSRE_TEST_PICKER_SELECTION_FILE"


def run_cli_command(
    console: Console,
    args: list[str],
    *,
    subprocess_timeout: float | None = None,
) -> bool:
    """Helper to delegate complex or interactive Click commands to a child process.

    ``subprocess_timeout`` caps how long ``subprocess.run`` waits before raising
    :class:`~subprocess.TimeoutExpired`. Interactive flows use ``None`` so the
    child can prompt as long as needed; callers that hit the network without a
    TTY (like ``opensre update``) pass a bounded timeout.

    Ctrl+C sends :exc:`KeyboardInterrupt`, which subclasses :exc:`BaseException`
    rather than :exc:`Exception`; it is handled here so the REPL survives and the
    child process exits on SIGINT alongside the interrupted ``run`` call.
    """
    console.print()
    cmd = [sys.executable, "-m", "app.cli", *args]
    try:
        result = subprocess.run(cmd, check=False, timeout=subprocess_timeout)
        if result.returncode != 0:
            console.print(f"[{ERROR}]CLI command exited with non-zero code {result.returncode}[/]")
    except subprocess.TimeoutExpired:
        console.print(f"[{ERROR}]error:[/] CLI command timed out")
    except KeyboardInterrupt:
        console.print(f"[{DIM}]CLI command cancelled (Ctrl+C).[/]")
    except Exception as exc:
        console.print(f"[{ERROR}]error running CLI command:[/] {exc}")
    console.print()
    return True


def _cmd_onboard(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["onboard", *args])


def _cmd_deploy(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["deploy", *args])


def _cmd_remote(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["remote", *args])


def _catalog_task_kind(command: list[str]) -> TaskKind:
    return TaskKind.SYNTHETIC_TEST if "synthetic" in command else TaskKind.CLI_COMMAND


def _argv_for_catalog_command(command: list[str]) -> list[str]:
    if command[:1] == ["opensre"]:
        return [sys.executable, "-m", "app.cli", *command[1:]]
    return command


def _start_test_command(
    *,
    session: ReplSession,
    console: Console,
    command: list[str],
    display_command: str | None = None,
) -> None:
    shown = display_command or shlex.join(command)
    session.record("cli_command", shown)
    start_background_cli_task(
        display_command=shown,
        argv_list=_argv_for_catalog_command(command),
        session=session,
        console=console,
        timeout_seconds=SYNTHETIC_TEST_TIMEOUT_SECONDS,
        kind=_catalog_task_kind(command),
        use_pty=True,
    )


def _run_test_picker_for_background(session: ReplSession, console: Console) -> bool:
    console.print()
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        prefix="opensre-test-selection-",
        suffix=".json",
        delete=False,
    )
    selection_path = Path(handle.name)
    handle.close()
    try:
        env = dict(os.environ)
        env[_TEST_PICKER_SELECTION_FILE_ENV] = str(selection_path)
        result = subprocess.run(
            [sys.executable, "-m", "app.cli", "tests"],
            check=False,
            env=env,
        )
        if result.returncode != 0:
            console.print(f"[{ERROR}]CLI command exited with non-zero code {result.returncode}[/]")
            console.print()
            return True
        if not selection_path.stat().st_size:
            console.print()
            return True
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
    finally:
        with contextlib.suppress(OSError):
            selection_path.unlink()

    if not isinstance(payload, list):
        console.print(f"[{ERROR}]test picker returned an invalid selection[/]")
        console.print()
        return True

    for item in payload:
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            continue
        display = item.get("command_display")
        _start_test_command(
            session=session,
            console=console,
            command=command,
            display_command=display if isinstance(display, str) else None,
        )
    console.print()
    return True


def _cmd_tests(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args:
        return _run_test_picker_for_background(session, console)

    subcommand = args[0].lower()
    if subcommand in _BACKGROUND_TEST_SUBCOMMANDS:
        _start_test_command(
            session=session,
            console=console,
            command=["opensre", "tests", *args],
        )
        return True

    if subcommand.startswith("-"):
        return run_cli_command(console, ["tests", *args])

    if subcommand not in _TEST_SUBCOMMANDS:
        suggestion = closest_choice(subcommand, _TEST_SUBCOMMANDS)
        if suggestion is None:
            console.print(
                f"[{ERROR}]unknown tests subcommand:[/] {escape(args[0])}  "
                "(try [bold]/tests list[/bold], [bold]/tests run <test_id>[/bold], "
                "[bold]/tests synthetic[/bold], or [bold]/tests cloudopsbench[/bold])"
            )
        else:
            console.print(
                f"[{ERROR}]unknown tests subcommand:[/] {escape(args[0])}  "
                f"Did you mean [bold]/tests {suggestion}[/bold]?"
            )
        session.mark_latest(ok=False, kind="slash")
        return True

    return run_cli_command(console, ["tests", *args])


def _cmd_guardrails(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["guardrails", *args])


def _cmd_update(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(
        console,
        ["update", *args],
        subprocess_timeout=_UPDATE_SUBPROCESS_TIMEOUT_SECONDS,
    )


def _cmd_uninstall(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["uninstall", *args])


def _cmd_config(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["config", *args])


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/onboard",
        "run the interactive onboarding wizard ('/onboard local_llm')",
        _cmd_onboard,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/deploy",
        "deploy OpenSRE to a cloud environment ('/deploy ec2|langsmith|railway')",
        _cmd_deploy,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/remote",
        "connect to and trigger a remote deployed agent ('/remote health|investigate|ops|pull|trigger')",
        _cmd_remote,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/tests",
        "browse and run inventoried tests ('/tests list|run|synthetic')",
        _cmd_tests,
        first_arg_completions=tuple((name, f"/tests {name}") for name in _TEST_SUBCOMMANDS),
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/guardrails",
        "manage sensitive information guardrail rules ('/guardrails audit|init|rules|test')",
        _cmd_guardrails,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/update",
        "check for a newer version and update if available",
        _cmd_update,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/uninstall",
        "remove opensre and all local data from this machine",
        _cmd_uninstall,
        execution_tier=ExecutionTier.ELEVATED,
    ),
    SlashCommand(
        "/config",
        "show or edit local OpenSRE config ('/config show|set <key> <value>')",
        _cmd_config,
        execution_tier=ExecutionTier.SAFE,
    ),
]
