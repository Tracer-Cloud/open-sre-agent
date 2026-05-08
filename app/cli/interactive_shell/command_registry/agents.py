"""Slash command: ``/agents`` (registered local AI agent fleet view).

Bare ``/agents`` renders the registered-agents dashboard; subcommands
drill into specific surfaces (currently ``conflicts``, with more
landing as the monitor-local-agents initiative ships).
"""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

from app.agents.conflicts import (
    DEFAULT_WINDOW_SECONDS,
    WriteEvent,
    detect_conflicts,
    render_conflicts,
)
from app.agents.registry import AgentRegistry
from app.cli.interactive_shell.agents_view import render_agents_table
from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ERROR

_AGENTS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("conflicts", "show file-write conflicts between local AI agents"),
)


def _opensre_agent_id() -> str:
    return f"opensre:{os.getpid()}"


def _cmd_agents_list(console: Console) -> bool:
    """Render the registered ``AgentRecord`` set as a Rich table.

    Bare ``/agents`` resolves here. Metric cells (``cpu%``,
    ``tokens/min``, ``$/hr``, ``status``, ``uptime``) render as
    placeholders until the wiring from #1490 / #1494 lands; this
    surface only consumes what the registry already holds today.
    """
    registry = AgentRegistry()
    table = render_agents_table(registry.list())
    console.print(table)
    return True


def _cmd_agents_conflicts(console: Console) -> bool:
    # Real write-event collection comes from #1500 (filesystem blast-radius
    # watcher), out of scope for this PR. Until that lands, the event source
    # is empty and `/agents conflicts` reports "no conflicts detected".
    events: list[WriteEvent] = []
    conflicts = detect_conflicts(
        events,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        opensre_agent_id=_opensre_agent_id(),
    )
    console.print(render_conflicts(conflicts))
    return True


def _cmd_agents(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args:
        return _cmd_agents_list(console)

    sub = args[0].lower().strip()

    if sub == "conflicts":
        return _cmd_agents_conflicts(console)

    console.print(
        f"[{TERMINAL_ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/agents[/bold] or [bold]/agents conflicts[/bold])"
    )
    session.mark_latest(ok=False, kind="slash")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/agents",
        "show registered local AI agents (subcommands: conflicts)",
        _cmd_agents,
        first_arg_completions=_AGENTS_FIRST_ARGS,
    ),
]

__all__ = ["COMMANDS"]
