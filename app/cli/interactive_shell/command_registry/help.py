"""Slash commands: /help and /?."""

from __future__ import annotations

from rich.console import Console

from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.rendering import repl_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import ACCENT_DIM, TERMINAL_ACCENT_BOLD, TEXT_DIM


def _cmd_help(_session: ReplSession, console: Console, _args: list[str]) -> bool:
    from app.cli.interactive_shell.command_registry.agents import COMMANDS as AGENTS_CMDS
    from app.cli.interactive_shell.command_registry.integrations import COMMANDS as INT_CMDS
    from app.cli.interactive_shell.command_registry.investigation import COMMANDS as INV_CMDS
    from app.cli.interactive_shell.command_registry.model import COMMANDS as MODEL_CMDS
    from app.cli.interactive_shell.command_registry.privacy_cmds import COMMANDS as PRIVACY_CMDS
    from app.cli.interactive_shell.command_registry.session_cmds import COMMANDS as SESSION_CMDS
    from app.cli.interactive_shell.command_registry.system import COMMANDS as SYS_CMDS
    from app.cli.interactive_shell.command_registry.tasks_cmds import COMMANDS as TASK_CMDS

    sections: list[tuple[str, list[SlashCommand]]] = [
        ("Help", list(COMMANDS)),
        ("Session", list(SESSION_CMDS)),
        ("Integrations & Models", list(INT_CMDS) + list(MODEL_CMDS)),
        ("Investigation", list(INV_CMDS)),
        ("Privacy", list(PRIVACY_CMDS)),
        ("Tasks", list(TASK_CMDS)),
        ("Agents", list(AGENTS_CMDS)),
        ("System", list(SYS_CMDS)),
    ]

    table = repl_table(title="Slash commands", title_style=TERMINAL_ACCENT_BOLD, show_header=False)
    table.add_column("name", no_wrap=True, min_width=18)
    table.add_column("description", style=TEXT_DIM)

    for section_name, cmds in sections:
        table.add_row(f"[{TERMINAL_ACCENT_BOLD}]{section_name}[/]", "")
        for i, cmd in enumerate(cmds):
            table.add_row(
                f"  [{ACCENT_DIM}]{cmd.name}[/]",
                cmd.help_text,
                end_section=(i == len(cmds) - 1),
            )

    console.print(table)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/help", "show available commands", _cmd_help, execution_tier=ExecutionTier.EXEMPT
    ),
    SlashCommand("/?", "shortcut for /help", _cmd_help, execution_tier=ExecutionTier.EXEMPT),
]

__all__ = ["COMMANDS"]
