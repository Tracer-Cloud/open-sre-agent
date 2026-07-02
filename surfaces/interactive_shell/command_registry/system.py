"""Slash commands: diagnostics, version, exit."""

from __future__ import annotations

from rich.console import Console

import platform
from platform.terminal.prompt_support import print_session_resume_hint
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    print_repl_table,
    repl_table,
)


def _cmd_exit(session: Session, console: Console, _args: list[str]) -> bool:
    if session.session_id:
        console.print()
        print_session_resume_hint(console, session.session_id)
    console.print(f"[{DIM}]goodbye.[/]")
    return False


def _cmd_health(_session: Session, console: Console, _args: list[str]) -> bool:
    from config.config import get_environment
    from integrations.store import STORE_PATH
    from integrations.verify import verify_integrations
    from surfaces.interactive_shell.ui.health import render_health_report

    results = verify_integrations()
    environment = get_environment().value
    render_health_report(
        console=console,
        environment=environment,
        integration_store_path=STORE_PATH,
        results=results,
    )
    return True


def _cmd_doctor(_session: Session, console: Console, _args: list[str]) -> bool:
    from surfaces.cli.commands.doctor import _CHECKS, _check

    status_styles: dict[str, str] = {"ok": HIGHLIGHT, "warn": WARNING, "error": ERROR}
    table = repl_table(title="OpenSRE Doctor\n", title_style=BOLD_BRAND)
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

    print_repl_table(console, table)
    if issues:
        console.print(f"[{WARNING}]{issues} issue(s) found.[/]")
    else:
        console.print(f"[{HIGHLIGHT}]all checks passed.[/]")
    return True


def _cmd_version(_session: Session, console: Console, _args: list[str]) -> bool:
    from config.version import get_version

    table = repl_table(title="Version info\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("opensre", get_version())
    table.add_row("python", platform.python_version())
    table.add_row("os", f"{platform.system().lower()} ({platform.machine()})")
    print_repl_table(console, table)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand("/exit", "Exit the interactive shell.", _cmd_exit),
    SlashCommand("/quit", "Alias for /exit.", _cmd_exit),
    SlashCommand("/health", "Show integration and agent health.", _cmd_health),
    SlashCommand("/doctor", "Run full environment diagnostic.", _cmd_doctor),
    SlashCommand("/version", "Print version, Python, and OS info.", _cmd_version),
]

__all__ = ["COMMANDS"]
