"""Slash commands: diagnostics, version, exit."""

from __future__ import annotations

import platform

from rich.console import Console

from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    repl_table,
)


def _cmd_exit(_session: ReplSession, console: Console, _args: list[str]) -> bool:
    console.print(f"[{DIM}]goodbye.[/]")
    return False


def _cmd_health(_session: ReplSession, console: Console, _args: list[str]) -> bool:
    from app.cli.support.health_view import render_health_report
    from app.config import get_environment
    from app.integrations.store import STORE_PATH
    from app.integrations.verify import verify_integrations

    results = verify_integrations()
    environment = get_environment().value
    render_health_report(
        console=console,
        environment=environment,
        integration_store_path=STORE_PATH,
        results=results,
    )
    return True


def _cmd_doctor(_session: ReplSession, console: Console, _args: list[str]) -> bool:
    from app.cli.commands.doctor import _CHECKS, _check

    status_styles: dict[str, str] = {"ok": HIGHLIGHT, "warn": WARNING, "error": ERROR}
    table = repl_table(title="OpenSRE Doctor", title_style=BOLD_BRAND)
    table.add_column("check", style="bold")
    table.add_column("status")
    table.add_column("detail", style=DIM, overflow="fold")

    issues = 0
    for name, fn in _CHECKS:
        result = _check(name, fn)
        status = result["status"]
        style = status_styles.get(status, DIM)
        table.add_row(name, f"[{style}]{status}[/]", result["detail"])
        if status in ("warn", "error"):
            issues += 1

    console.print(table)
    if issues:
        console.print(f"[{WARNING}]{issues} issue(s) found.[/]")
    else:
        console.print(f"[{HIGHLIGHT}]all checks passed.[/]")
    return True


def _cmd_version(_session: ReplSession, console: Console, _args: list[str]) -> bool:
    from app.version import get_version

    table = repl_table(title="Version info", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("opensre", get_version())
    table.add_row("python", platform.python_version())
    table.add_row("os", f"{platform.system().lower()} ({platform.machine()})")
    console.print(table)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/exit", "exit the interactive shell", _cmd_exit, execution_tier=ExecutionTier.EXEMPT
    ),
    SlashCommand("/quit", "alias for /exit", _cmd_exit, execution_tier=ExecutionTier.EXEMPT),
    SlashCommand("/health", "show integration and agent health", _cmd_health),
    SlashCommand("/doctor", "run full environment diagnostic", _cmd_doctor),
    SlashCommand("/version", "print version, Python and OS info", _cmd_version),
]

__all__ = ["COMMANDS"]
