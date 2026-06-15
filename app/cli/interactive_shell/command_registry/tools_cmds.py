"""Slash command /tools."""

from __future__ import annotations

from rich.console import Console

from app.cli.interactive_shell.command_registry.types import (
    ExecutionTier,
    SlashCommand,
    make_list_root_handler,
)
from app.cli.interactive_shell.config.tool_catalog import build_tool_catalog
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import render_tools_table


def _list_tools(_session: ReplSession, console: Console, _args: list[str]) -> bool:
    render_tools_table(console, build_tool_catalog())
    return True


_cmd_tools = make_list_root_handler(
    "/tools",
    _list_tools,
    list_aliases=("list", "ls", "tool", "tools"),
)

_TOOLS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list registered tools (investigation + chat surfaces)"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/tools",
        "List registered tools.",
        _cmd_tools,
        usage=("/tools", "/tools list"),
        first_arg_completions=_TOOLS_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    )
]

__all__ = ["COMMANDS", "_TOOLS_FIRST_ARGS", "_cmd_tools"]
