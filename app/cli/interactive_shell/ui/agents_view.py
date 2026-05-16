"""Rich-table rendering for the ``/agents`` slash-command dashboard.

Produces the structural shape of the dashboard with the columns
documented in #1486's preview (``agent``, ``pid``, ``uptime``,
``cpu%``, ``tokens/min``, ``$/hr``, ``status``). The ``$/hr`` cell
reads from ``agents.yaml`` via :func:`app.agents.config.load_agents_config`;
``cpu%`` / ``uptime`` / ``status`` are populated from the per-PID
sampler. The ``tokens/min`` column still renders as ``-``
until the token-meter consumer lands in a future issue.

This module lives outside ``app/agents/`` deliberately: the agents
package is for *collectors* (probe, registry, sweep, meters) and
must not depend on Rich (a UI library), or non-CLI consumers of the
collectors would pull it in transitively. The slash command in
``command_registry/agents.py`` is the one and only consumer.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from rich.console import JustifyMethod
from rich.markup import escape
from rich.table import Table

from app.agents.config import load_agents_config
from app.agents.registry import AgentRecord
from app.agents.sampler import get_snapshot
from app.cli.interactive_shell.ui.theme import BOLD_BRAND

# Placeholder for columns without a live data source (currently only tokens/min).
_UNFILLED = "-"

#: Columns the dashboard ships with. Order matches the user-facing table.
#: Re-using Rich's own ``JustifyMethod`` type alias rather than a
#: hand-maintained Literal so column-justify options stay in lockstep
#: with the library if Rich ever expands them.
_COLUMNS: tuple[tuple[str, JustifyMethod], ...] = (
    ("agent", "left"),
    ("pid", "right"),
    ("uptime", "right"),
    ("cpu%", "right"),
    ("tokens/min", "right"),
    ("$/hr", "right"),
    ("status", "left"),
)


def _format_uptime(delta: timedelta) -> str:
    """Format a timedelta as a compact human-readable duration string."""
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    hours = total_seconds // 3600
    if hours < 24:
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h{minutes}m"
    days = hours // 24
    remaining_hours = hours % 24
    return f"{days}d{remaining_hours}h"


def render_agents_table(records: Iterable[AgentRecord]) -> Table:
    """Return a Rich ``Table`` for the registered ``AgentRecord`` set.

    The returned table always has the full column structure, even
    when no records exist; the caller passes it to ``console.print()``.
    An empty record list produces a table with no body rows and an
    explanatory caption.

    The ``$/hr`` cell reads ``hourly_budget_usd`` from ``agents.yaml``
    when configured. The ``uptime``, ``cpu%``,``status`` are read
    from the background sampler's probe snapshots. ``tokens/min`` is still
    pending a future token-meter consumer.
    """
    materialized = list(records)
    table = Table(
        title="agents",
        title_style=BOLD_BRAND,
        caption="no agents discovered or registered yet" if not materialized else None,
    )
    for header, justify in _COLUMNS:
        table.add_column(header, justify=justify)
    # Load once per render: agents.yaml is small and the dashboard is
    # invoked interactively, so a single read per ``/agents`` invocation
    # is cheaper than caching with invalidation. A schema-invalid file
    # falls back to empty budgets here (``$/hr`` cells render as ``-``)
    # rather than crashing the dashboard with a raw traceback — the
    # same hand-edit surfaces a friendly error in ``/agents budget``,
    # which is the surface that exists to fix it.
    try:
        budgets = load_agents_config().agents
    except ValidationError:
        budgets = {}
    now = datetime.now(UTC)
    for record in materialized:
        budget = budgets.get(record.name)
        hourly_cell = (
            f"${budget.hourly_budget_usd:.2f}"
            if budget is not None and budget.hourly_budget_usd is not None
            else _UNFILLED
        )
        snapshot = get_snapshot(record.pid)
        if snapshot is not None:
            uptime_cell = _format_uptime(now - snapshot.started_at)
            cpu_cell = f"{snapshot.cpu_percent:.1f}"
            status_cell = snapshot.status
        else:
            uptime_cell = _UNFILLED
            cpu_cell = _UNFILLED
            status_cell = _UNFILLED
        table.add_row(
            escape(record.name),
            str(record.pid),
            uptime_cell,  # uptime
            cpu_cell,  # cpu%
            _UNFILLED,  # tokens/min
            hourly_cell,
            status_cell,
        )
    return table


__all__ = ["render_agents_table"]
