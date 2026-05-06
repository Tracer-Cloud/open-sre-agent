"""Slash commands: /help and /?."""

from __future__ import annotations

from rich.console import Console

from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.rendering import repl_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD


def _cmd_help(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # Import after the registry is fully assembled (avoids circular import at module load).
    from app.cli.interactive_shell.command_registry import SLASH_COMMANDS

    table = repl_table(title="Slash commands", title_style=TERMINAL_ACCENT_BOLD, show_header=False)
    table.add_column("name", style="bold")
    table.add_column("description", style="dim")
    for cmd in SLASH_COMMANDS.values():
        table.add_row(cmd.name, cmd.help_text)
    console.print(table)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/help", "show available commands", _cmd_help, execution_tier=ExecutionTier.EXEMPT
    ),
    SlashCommand("/?", "shortcut for /help", _cmd_help, execution_tier=ExecutionTier.EXEMPT),
]

__all__ = ["COMMANDS"]
