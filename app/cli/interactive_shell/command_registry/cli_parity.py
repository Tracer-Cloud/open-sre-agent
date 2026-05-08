"""Slash commands for CLI parity, delegating to the Click CLI via subprocess."""

from __future__ import annotations

import subprocess
import sys

from rich.console import Console

from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.session import ReplSession

_UPDATE_SUBPROCESS_TIMEOUT_SECONDS = 300


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
            console.print(f"[red]CLI command exited with non-zero code {result.returncode}[/red]")
    except subprocess.TimeoutExpired:
        console.print("[red]error:[/red] CLI command timed out")
    except KeyboardInterrupt:
        console.print("[dim]CLI command cancelled (Ctrl+C).[/dim]")
    except Exception as exc:
        console.print(f"[red]error running CLI command:[/red] {exc}")
    console.print()
    return True


def _cmd_onboard(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["onboard", *args])


def _cmd_deploy(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["deploy", *args])


def _cmd_remote(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    return run_cli_command(console, ["remote", *args])


def _cmd_tests(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
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
