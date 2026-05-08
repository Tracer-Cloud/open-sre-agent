"""Rich-table rendering for the ``/agents`` slash-command dashboard.

Produces the structural shape of the dashboard with the columns
documented in #1486's preview (``agent``, ``pid``, ``uptime``,
``cpu%``, ``tokens/min``, ``$/hr``, ``status``). Cells that aren't
yet wired (every metric column for now) render as ``-`` — the
sampler in #1490 fills ``cpu%`` / ``uptime`` / ``status`` later, the
token meter consumer in #1490+ fills ``tokens/min``, and the budget
loader from #1494 fills ``$/hr``.

This module lives outside ``app/agents/`` deliberately: the agents
package is for *collectors* (probe, registry, sweep, meters) and
must not depend on Rich (a UI library), or non-CLI consumers of the
collectors would pull it in transitively. The slash command in
``command_registry/agents.py`` is the one and only consumer.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from rich.markup import escape
from rich.table import Table

from app.agents.registry import AgentRecord
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD

# Cells we don't yet have a data source for. See module docstring for
# which downstream issues fill which column.
_UNFILLED = "-"

# Rich's ``Table.add_column`` only accepts this restricted Literal
# for ``justify`` — narrowing the local alias keeps mypy happy
# without sprinkling ignore comments.
_Justify = Literal["default", "left", "center", "right", "full"]

#: Columns the dashboard ships with. Order is the user-facing order
#: and is also the cell-injection contract that #1490 will lean on
#: when it threads probe snapshots into the rendering layer.
_COLUMNS: tuple[tuple[str, _Justify], ...] = (
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
    explanatory caption pointing the user at the registration command.

    Cells for metric columns (``uptime``, ``cpu%``, ``tokens/min``,
    ``$/hr``, ``status``) render as ``-`` placeholders. Filling them
    is intentionally out of scope here — see module docstring for the
    issue map.
    """
    materialized = list(records)
    table = Table(
        title="agents",
        title_style=TERMINAL_ACCENT_BOLD,
        caption=(
            "no agents registered — use `opensre agents register` to track one"
            if not materialized
            else None
        ),
    )
    for header, justify in _COLUMNS:
        table.add_column(header, justify=justify)
    for record in materialized:
        table.add_row(
            escape(record.name),
            str(record.pid),
            _UNFILLED,  # uptime
            _UNFILLED,  # cpu%
            _UNFILLED,  # tokens/min
            _UNFILLED,  # $/hr
            _UNFILLED,  # status
        )
    return table


__all__ = ["render_agents_table"]
