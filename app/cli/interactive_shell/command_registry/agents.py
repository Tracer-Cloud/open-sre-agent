"""Slash command: ``/agents`` (registered local AI agent fleet view).

Bare ``/agents`` renders the registered-agents dashboard; subcommands
drill into specific surfaces (``budget``, ``conflicts``, with more
landing as the monitor-local-agents initiative ships).
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape

from app.agents.config import (
    agents_config_path,
    load_agents_config,
    set_agent_budget,
)
from app.agents.conflicts import (
    DEFAULT_WINDOW_SECONDS,
    WriteEvent,
    detect_conflicts,
    render_conflicts,
)
from app.agents.registry import AgentRegistry
from app.cli.interactive_shell.agents_view import render_agents_table
from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.rendering import repl_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import BOLD_BRAND, DIM, ERROR

_AGENTS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("budget", "view or edit per-agent hourly budgets"),
    ("conflicts", "show file-write conflicts between local AI agents"),
)


def _opensre_agent_id() -> str:
    return f"opensre:{os.getpid()}"


def _cmd_agents_list(console: Console) -> bool:
    """Render the registered ``AgentRecord`` set as a Rich table.

    Bare ``/agents`` resolves here. The ``$/hr`` cell reads
    ``hourly_budget_usd`` from ``agents.yaml``; the remaining metric
    cells (``cpu%``, ``tokens/min``, ``status``, ``uptime``) still
    render as placeholders until the per-PID sampler and token-meter
    consumer from #1490 land.
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


def _display_path(path: Path) -> str:
    """Replace the user's home prefix with ``~`` for cleaner CLI output."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def _print_config_error(console: Console, exc: ValidationError) -> None:
    console.print(f"[{ERROR}]agents.yaml has invalid contents:[/] {escape(str(exc))}")


def _cmd_agents_budget(session: ReplSession, console: Console, args: list[str]) -> bool:
    """View or edit per-agent budgets stored in ``~/.config/opensre/agents.yaml``.

    No args → render the current budgets as a table. Two args
    (``<agent> <usd>``) → set ``hourly_budget_usd`` for that agent and
    persist. Anything else → usage hint.
    """
    if not args:
        try:
            config = load_agents_config()
        except ValidationError as exc:
            _print_config_error(console, exc)
            session.mark_latest(ok=False, kind="slash")
            return True
        if not config.agents:
            console.print(
                f"[{DIM}]no per-agent budgets configured.[/]  "
                "use [bold]/agents budget <agent> <usd>[/bold] to set one."
            )
            return True
        table = repl_table(title="agent budgets", title_style=BOLD_BRAND)
        table.add_column("agent", style="bold")
        table.add_column("hourly $", justify="right")
        table.add_column("progress min", justify="right")
        table.add_column("error %", justify="right")
        for name in sorted(config.agents):
            budget = config.agents[name]
            table.add_row(
                escape(name),
                f"${budget.hourly_budget_usd:.2f}" if budget.hourly_budget_usd is not None else "-",
                str(budget.progress_minutes) if budget.progress_minutes is not None else "-",
                f"{budget.error_rate_pct:.1f}" if budget.error_rate_pct is not None else "-",
            )
        console.print(table)
        return True

    if len(args) != 2:
        console.print(f"[{ERROR}]usage:[/] /agents budget [<agent> <usd>]")
        session.mark_latest(ok=False, kind="slash")
        return True

    name = args[0].strip()
    raw_usd = args[1]
    try:
        usd = float(raw_usd)
    except ValueError:
        console.print(f"[{ERROR}]invalid budget:[/] {escape(raw_usd)} is not a number")
        session.mark_latest(ok=False, kind="slash")
        return True
    # ``nan`` and ``inf`` slip past ``usd <= 0`` because both
    # ``float("nan") <= 0`` and ``float("inf") <= 0`` are ``False``.
    # Without this guard a stored ``nan`` would corrupt agents.yaml
    # (next load fails Pydantic's ``gt=0`` since ``nan > 0`` is
    # ``False``) and ``inf`` would render as ``$inf`` in the dashboard.
    if not math.isfinite(usd) or usd <= 0:
        console.print(f"[{ERROR}]invalid budget:[/] must be a positive finite number")
        session.mark_latest(ok=False, kind="slash")
        return True

    try:
        set_agent_budget(name, usd)
    except ValidationError as exc:
        _print_config_error(console, exc)
        session.mark_latest(ok=False, kind="slash")
        return True

    console.print(
        f"updated [bold]{escape(name)}[/]: ${usd:.2f}/hr → {_display_path(agents_config_path())}"
    )
    return True


def _cmd_agents(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args:
        return _cmd_agents_list(console)

    sub = args[0].lower().strip()

    if sub == "budget":
        return _cmd_agents_budget(session, console, args[1:])
    if sub == "conflicts":
        return _cmd_agents_conflicts(console)

    console.print(
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/agents[/bold], [bold]/agents budget[/bold], "
        "or [bold]/agents conflicts[/bold])"
    )
    session.mark_latest(ok=False, kind="slash")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/agents",
        "show registered local AI agents (subcommands: budget, conflicts)",
        _cmd_agents,
        first_arg_completions=_AGENTS_FIRST_ARGS,
    ),
]

__all__ = ["COMMANDS"]
