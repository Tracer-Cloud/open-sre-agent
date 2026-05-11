"""Rich-table rendering for the ``/agents`` slash-command dashboard.

Produces the structural shape of the dashboard with the columns
documented in #1486's preview (``agent``, ``pid``, ``uptime``,
``cpu%``, ``tokens/min``, ``$/hr``, ``status``). The ``$/hr`` cell
reads from ``agents.yaml`` via :func:`app.agents.config.load_agents_config`;
the ``status`` cell shows whether the row came from the explicit
registry or read-only process discovery; remaining metric columns still
render as ``-`` until #1490 wires the per-PID sampler and token-meter
consumer.

This module lives outside ``app/agents/`` deliberately: the agents
package is for *collectors* (probe, registry, sweep, meters) and
must not depend on Rich (a UI library), or non-CLI consumers of the
collectors would pull it in transitively. The slash command in
``command_registry/agents.py`` is the one and only consumer.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError
from rich.console import JustifyMethod
from rich.markup import escape
from rich.table import Table

from app.agents.config import load_agents_config
from app.agents.registry import AgentRecord
from app.cli.interactive_shell.ui.theme import BOLD_BRAND

# Cells we don't yet have a data source for. See module docstring for
# which downstream issues fill which column.
_UNFILLED = "-"

#: Columns the dashboard ships with. Order is the user-facing order
#: and is also the cell-injection contract that #1490 will lean on
#: when it threads probe snapshots into the rendering layer.
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


def render_agents_table(records: Iterable[AgentRecord]) -> Table:
    """Return a Rich ``Table`` for the registered ``AgentRecord`` set.

    The returned table always has the full column structure, even
    when no records exist; the caller passes it to ``console.print()``.
    An empty record list produces a table with no body rows and an
    explanatory caption.

    The ``$/hr`` cell reads ``hourly_budget_usd`` from ``agents.yaml``
    when configured. The other metric cells (``uptime``, ``cpu%``,
    ``tokens/min``) still render as ``-`` placeholders; filling them is
    out of scope here.
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
    for record in materialized:
        budget = budgets.get(record.name)
        hourly_cell = (
            f"${budget.hourly_budget_usd:.2f}"
            if budget is not None and budget.hourly_budget_usd is not None
            else _UNFILLED
        )
        table.add_row(
            escape(record.name),
            str(record.pid),
            _UNFILLED,  # uptime
            _UNFILLED,  # cpu%
            _UNFILLED,  # tokens/min
            hourly_cell,
            escape(record.source),
        )
    return table


__all__ = ["render_agents_table"]
