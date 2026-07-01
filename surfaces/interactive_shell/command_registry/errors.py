"""Centralized error formatting for slash commands."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any

from rich.console import Console
from rich.markup import escape as _rich_escape

from platform.terminal.theme import DIM, ERROR
from surfaces.interactive_shell.runtime import ReplSession
from surfaces.interactive_shell.ui.components.rendering import repl_print


def print_unknown_subcommand(
    console: Console,
    command_name: str,
    sub: str,
    usage_hints: Iterable[tuple[str, str]],
) -> None:
    """Print standard error for an unknown subcommand."""
    lines = [f"[{ERROR}]❌ Unknown subcommand:[/] '{_rich_escape(sub)}'"]
    if usage_hints:
        lines.append(f"[{DIM}]Usage:[/]")
        for hint_sub, desc in usage_hints:
            cmd_full = f"{command_name} {hint_sub}"
            lines.append(f"  {cmd_full:<26} — {desc}")

    repl_print(console, "\n".join(lines))


def print_command_usage(
    console: Console,
    command_name: str,
    usage_hints: Iterable[tuple[str, str]],
) -> None:
    """Print standard usage block (e.g. for missing arguments)."""
    lines = [f"[{DIM}]Usage:[/]"]
    for hint_sub, desc in usage_hints:
        cmd_full = f"{command_name} {hint_sub}"
        lines.append(f"  {cmd_full:<26} — {desc}")

    repl_print(console, "\n".join(lines))


def unknown_subcommand_handler(
    command_name: str,
    usage_hints: Iterable[tuple[str, str]],
) -> Callable[[ReplSession, Console, str], bool]:
    """Return a generic fallback handler for an unknown subcommand."""

    def fallback(session: ReplSession, console: Console, sub: str) -> bool:
        print_unknown_subcommand(console, command_name, sub, usage_hints)
        session.mark_latest(ok=False, kind="slash")
        return True

    return fallback


def no_output_guard(
    command_name: str,
    fallback_message: str,
) -> Callable[[Callable[[ReplSession, Console, list[str]], bool]], Callable[[ReplSession, Console, list[str]], bool]]:
    """Decorator to capture console output; print a fallback if nothing was written."""

    def decorator(func: Callable[[ReplSession, Console, list[str]], bool]) -> Callable[[ReplSession, Console, list[str]], bool]:
        @wraps(func)
        def wrapper(
            session: ReplSession,
            console: Console,
            args: list[str],
        ) -> bool:
            from surfaces.interactive_shell.utils.telemetry.console_capture import (
                capture_console_segment,
            )

            with capture_console_segment(console) as get_cap:
                result = func(session, console, args)

            if not get_cap().strip():
                repl_print(
                    console, f"[{DIM}]No output produced for {command_name}. {fallback_message}[/]"
                )
            return result

        return wrapper

    return decorator
