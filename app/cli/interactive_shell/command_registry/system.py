"""Slash commands: diagnostics, version, exit."""

from __future__ import annotations

import platform

from rich.console import Console

from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.rendering import repl_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD


def _cmd_exit(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    console.print("[dim]goodbye.[/dim]")
    return False


def _cmd_health(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
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


def _cmd_doctor(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from app.cli.commands.doctor import _CHECKS, _check

    status_styles: dict[str, str] = {"ok": "green", "warn": "yellow", "error": "red"}
    table = repl_table(title="OpenSRE Doctor", title_style=TERMINAL_ACCENT_BOLD)
    table.add_column("check", style="bold")
    table.add_column("status")
    table.add_column("detail", style="dim", overflow="fold")

    issues = 0
    for name, fn in _CHECKS:
        result = _check(name, fn)
        status = result["status"]
        style = status_styles.get(status, "dim")
        table.add_row(name, f"[{style}]{status}[/{style}]", result["detail"])
        if status in ("warn", "error"):
            issues += 1

    console.print(table)
    if issues:
        console.print(f"[yellow]{issues} issue(s) found.[/yellow]")
    else:
        console.print("[green]all checks passed.[/green]")
    return True


def _cmd_version(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from app.version import get_version

    table = repl_table(title="Version info", title_style=TERMINAL_ACCENT_BOLD, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("opensre", get_version())
    table.add_row("python", platform.python_version())
    table.add_row("os", f"{platform.system().lower()} ({platform.machine()})")
    console.print(table)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand("/exit", "exit the interactive shell", _cmd_exit),
    SlashCommand("/quit", "alias for /exit", _cmd_exit),
    SlashCommand("/health", "show integration and agent health", _cmd_health),
    SlashCommand("/doctor", "run full environment diagnostic", _cmd_doctor),
    SlashCommand("/version", "print version, Python and OS info", _cmd_version),
]

__all__ = ["COMMANDS"]
