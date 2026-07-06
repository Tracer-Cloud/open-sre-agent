"""Slash command /tools."""

from __future__ import annotations

from rich.console import Console

from platform.terminal.theme import DIM, ERROR, HIGHLIGHT
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import render_tools_table
from surfaces.interactive_shell.ui.components.rendering import repl_print
from surfaces.interactive_shell.ui.tables.tool_catalog import (
    ToolCatalogEntry,
    build_tool_catalog,
)

_LIST_ALIASES: frozenset[str] = frozenset({"list", "ls", "tool", "tools"})


def _matches(entry: ToolCatalogEntry, query: str) -> bool:
    haystack = f"{entry.name} {entry.description}".lower()
    return query in haystack


def _filter_catalog(entries: list[ToolCatalogEntry], query: str) -> list[ToolCatalogEntry]:
    q = query.strip().lower()
    if not q:
        return entries
    return [entry for entry in entries if _matches(entry, q)]


def _render_count(console: Console, entries: list[ToolCatalogEntry]) -> None:
    if not entries:
        repl_print(console, f"[{DIM}]no tools registered.[/]")
        return
    total = len(entries)
    per_surface: dict[str, int] = {}
    for entry in entries:
        for surface in entry.surfaces:
            per_surface[surface] = per_surface.get(surface, 0) + 1
    repl_print(console, f"[{HIGHLIGHT}]{total}[/] registered tool{'s' if total != 1 else ''}")
    for surface in sorted(per_surface):
        repl_print(console, f"  [{DIM}]{surface}[/]: {per_surface[surface]}")


def _list_tools(session: Session, console: Console, args: list[str]) -> bool:
    entries = build_tool_catalog()
    if args:
        query = " ".join(args)
        filtered = _filter_catalog(entries, query)
        if not filtered:
            repl_print(
                console,
                f"[{DIM}]no tools match[/] {query!r}. "
                f"Try [bold]/tools[/bold] to see all, or [bold]/tools count[/bold] for a summary.",
            )
            session.mark_latest(ok=True, kind="slash")
            return True
        render_tools_table(console, filtered)
        return True
    render_tools_table(console, entries)
    return True


def _cmd_tools(session: Session, console: Console, args: list[str]) -> bool:
    if not args:
        return _list_tools(session, console, [])

    sub = args[0].lower().strip()
    rest = args[1:]

    if sub in _LIST_ALIASES:
        return _list_tools(session, console, rest)
    if sub == "count":
        _render_count(console, build_tool_catalog())
        return True
    if sub == "search":
        if not rest:
            repl_print(console, f"[{ERROR}]usage:[/] /tools search <query>")
            session.mark_latest(ok=False, kind="slash")
            return True
        return _list_tools(session, console, rest)

    # Bare query — treat unknown args as a substring filter so `/tools slack`
    # narrows the list instead of erroring. Slash-command completion still
    # advertises the named subcommands via ``_TOOLS_FIRST_ARGS`` below.
    return _list_tools(session, console, args)


_TOOLS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list registered tools (investigation + chat surfaces)"),
    ("ls", "alias for list"),
    ("count", "print total tool count and per-surface breakdown"),
    ("search", "filter tools by substring: /tools search <query>"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/tools",
        "List registered tools; optionally filter or summarise.",
        _cmd_tools,
        usage=(
            "/tools",
            "/tools list",
            "/tools count",
            "/tools search <query>",
            "/tools <query>",
        ),
        first_arg_completions=_TOOLS_FIRST_ARGS,
    )
]

__all__ = ["COMMANDS", "_TOOLS_FIRST_ARGS", "_cmd_tools"]
