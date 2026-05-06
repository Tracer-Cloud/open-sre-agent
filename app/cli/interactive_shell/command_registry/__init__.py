"""Composable slash-command registry for the interactive REPL."""

from __future__ import annotations

from itertools import chain

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry.help import COMMANDS as HELP_COMMANDS
from app.cli.interactive_shell.command_registry.integrations import (
    COMMANDS as INTEGRATIONS_COMMANDS,
)
from app.cli.interactive_shell.command_registry.investigation import (
    COMMANDS as INVESTIGATION_COMMANDS,
)
from app.cli.interactive_shell.command_registry.model import COMMANDS as MODEL_COMMANDS
from app.cli.interactive_shell.command_registry.model import (
    switch_llm_provider,
    switch_toolcall_model,
)
from app.cli.interactive_shell.command_registry.repl_data import (
    load_llm_settings,
    load_verified_integrations,
)
from app.cli.interactive_shell.command_registry.session_cmds import COMMANDS as SESSION_COMMANDS
from app.cli.interactive_shell.command_registry.system import COMMANDS as SYSTEM_COMMANDS
from app.cli.interactive_shell.command_registry.tasks_cmds import COMMANDS as TASK_COMMANDS
from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ERROR

_MERGED_SEQUENCE = tuple(
    chain(
        HELP_COMMANDS,
        SESSION_COMMANDS,
        INTEGRATIONS_COMMANDS,
        MODEL_COMMANDS,
        INVESTIGATION_COMMANDS,
        TASK_COMMANDS,
        SYSTEM_COMMANDS,
    )
)

SLASH_COMMANDS: dict[str, SlashCommand] = {cmd.name: cmd for cmd in _MERGED_SEQUENCE}


def dispatch_slash(command_line: str, session: ReplSession, console: Console) -> bool:
    """Dispatch a slash command line. Returns False iff the REPL should exit."""
    stripped = command_line.strip()
    if stripped == "/":
        from app.cli.interactive_shell.command_registry.help import _cmd_help

        return _cmd_help(session, console, [])

    parts = stripped.split()
    if not parts:
        return True
    name = parts[0].lower()
    args = parts[1:]
    cmd = SLASH_COMMANDS.get(name)
    if cmd is None:
        console.print()
        console.print(
            f"[{TERMINAL_ERROR}]unknown command:[/] {escape(name)}  (type [bold]/help[/bold])"
        )
        return True
    return cmd.handler(session, console, args)


__all__ = [
    "SLASH_COMMANDS",
    "SlashCommand",
    "dispatch_slash",
    "load_llm_settings",
    "load_verified_integrations",
    "switch_llm_provider",
    "switch_toolcall_model",
]
