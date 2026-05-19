"""Slash commands: /help and /?."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import ERROR
from app.cli.interactive_shell.ui.choice_menu import repl_tty_interactive
from app.cli.interactive_shell.ui.help_menu import (
    HelpSection,
    choose_help_command,
    render_command_detail,
    render_help_index,
    render_section_detail,
)


def _raw_help_sections() -> list[HelpSection]:
    from app.cli.interactive_shell.command_registry.agents import COMMANDS as AGENTS_CMDS
    from app.cli.interactive_shell.command_registry.alerts import COMMANDS as ALERTS_CMDS
    from app.cli.interactive_shell.command_registry.cli_parity import (
        COMMANDS as PARITY_COMMANDS,
    )
    from app.cli.interactive_shell.command_registry.integrations import COMMANDS as INT_CMDS
    from app.cli.interactive_shell.command_registry.investigation import COMMANDS as INV_CMDS
    from app.cli.interactive_shell.command_registry.model import COMMANDS as MODEL_CMDS
    from app.cli.interactive_shell.command_registry.privacy_cmds import COMMANDS as PRIVACY_CMDS
    from app.cli.interactive_shell.command_registry.session_cmds import COMMANDS as SESSION_CMDS
    from app.cli.interactive_shell.command_registry.system import COMMANDS as SYS_CMDS
    from app.cli.interactive_shell.command_registry.tasks_cmds import COMMANDS as TASK_CMDS
    from app.cli.interactive_shell.command_registry.watch_cmds import COMMANDS as WATCH_CMDS

    return [
        ("Help", list(COMMANDS)),
        ("Session", list(SESSION_CMDS)),
        ("Integrations & Models", list(INT_CMDS) + list(MODEL_CMDS)),
        ("Investigation", list(INV_CMDS)),
        ("Privacy", list(PRIVACY_CMDS)),
        ("Tasks", list(TASK_CMDS) + list(WATCH_CMDS)),
        ("Agents", list(AGENTS_CMDS)),
        ("Alerts", list(ALERTS_CMDS)),
        ("CLI (parity)", list(PARITY_COMMANDS)),
        ("System", list(SYS_CMDS)),
    ]


def _help_sections() -> list[HelpSection]:
    """Return user-visible help sections with duplicate command names hidden."""
    seen: set[str] = set()
    sections: list[HelpSection] = []
    for section_name, commands in _raw_help_sections():
        visible: list[SlashCommand] = []
        for command in commands:
            if command.name in seen:
                continue
            seen.add(command.name)
            visible.append(command)
        sections.append((section_name, visible))
    return sections


def _find_command(sections: Sequence[HelpSection], target: str) -> SlashCommand | None:
    normalized = target.strip().lower()
    if not normalized:
        return None
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    for _section_name, commands in sections:
        for command in commands:
            if command.name.lower() == normalized:
                return command
    return None


def _find_section(
    sections: Sequence[HelpSection],
    target: str,
) -> tuple[str, Sequence[SlashCommand]] | None:
    normalized = target.strip().lower().replace("-", " ")
    for section_name, commands in sections:
        aliases = {
            section_name.lower(),
            section_name.lower().replace("&", "and"),
            section_name.lower().replace(" & ", " "),
        }
        if normalized in aliases:
            return section_name, commands
    return None


def _cmd_help(_session: ReplSession, console: Console, args: list[str]) -> bool:
    sections = _help_sections()
    if args:
        target = " ".join(args).strip()
        if target.lower() in {"all", "commands"}:
            render_help_index(console, sections)
            return True
        if not target.startswith("/"):
            section = _find_section(sections, target)
            if section is not None:
                section_name, commands = section
                render_section_detail(console, section_name, commands)
                return True
        command = _find_command(sections, target)
        if command is not None:
            render_command_detail(console, command)
            return True
        console.print(f"[{ERROR}]unknown help topic:[/] {escape(target)}")
        console.print(
            "Try [bold]/help[/bold], [bold]/help /model[/bold], or [bold]/help tasks[/bold]."
        )
        return True

    if repl_tty_interactive():
        choose_help_command(sections)
        return True

    render_help_index(console, sections)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/help",
        "Show available commands.",
        _cmd_help,
        usage=("/help", "/help <command>", "/help <category>"),
        examples=("/help /model", "/help tasks"),
        execution_tier=ExecutionTier.EXEMPT,
    ),
    SlashCommand("/?", "Shortcut for /help.", _cmd_help, execution_tier=ExecutionTier.EXEMPT),
]

__all__ = ["COMMANDS"]
