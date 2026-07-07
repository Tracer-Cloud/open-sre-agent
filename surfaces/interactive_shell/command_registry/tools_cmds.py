"""Slash command /tools."""

from __future__ import annotations

from rich.console import Console

from surfaces.interactive_shell.command_registry.types import (
    SlashCommand,
    make_list_root_handler,
)
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import render_tools_table
from surfaces.interactive_shell.ui.tables.tool_catalog import build_tool_catalog

# Rough vertical cost per row: the table renders with `show_lines=True`, so
# each entry uses a content line plus a divider. Description cells often wrap,
# so budget one extra line per entry. Header, footer, and title account for
# the leading constant.
_ROWS_PER_ENTRY = 3
_TABLE_CHROME_ROWS = 4


def _estimate_table_height(entry_count: int) -> int:
    return _TABLE_CHROME_ROWS + entry_count * _ROWS_PER_ENTRY


def _should_page(entry_count: int, terminal_height: int, *, is_terminal: bool) -> bool:
    """Return True when the rendered tools table won't fit in the current terminal.

    The pager is only useful on a real TTY: piped output, headless test runs,
    and CI logs should always see the raw table so downstream tooling can
    parse it.
    """
    if not is_terminal or terminal_height <= 0:
        return False
    return _estimate_table_height(entry_count) > terminal_height


def _list_tools(_session: Session, console: Console, _args: list[str]) -> bool:
    entries = build_tool_catalog()
    if not entries:
        render_tools_table(console, entries)
        return True
    terminal_height = getattr(console.size, "height", 0) or 0
    if _should_page(
        len(entries), terminal_height, is_terminal=bool(getattr(console, "is_terminal", False))
    ):
        with console.pager(styles=True):
            render_tools_table(console, entries)
    else:
        render_tools_table(console, entries)
    return True


_cmd_tools = make_list_root_handler(
    "/tools",
    _list_tools,
    list_aliases=("list", "ls", "tool", "tools"),
)

_TOOLS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list registered tools (investigation + chat surfaces)"),
    ("ls", "alias for list"),
    ("tool", "alias for list"),
    ("tools", "alias for list"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/tools",
        "List registered tools.",
        _cmd_tools,
        usage=("/tools", "/tools list"),
        first_arg_completions=_TOOLS_FIRST_ARGS,
    )
]

__all__ = ["COMMANDS", "_TOOLS_FIRST_ARGS", "_cmd_tools"]
