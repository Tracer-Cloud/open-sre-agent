"""Rich presentation for fleet file-write conflict detection results."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.markup import escape
from rich.table import Table

import platform.terminal.theme as ui_theme
from surfaces.interactive_shell.ui.components.rendering import repl_table
from tools.fleet_monitoring.conflicts import FileWriteConflict

_EMPTY_STATE = "no conflicts detected"


def _format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%H:%M:%S UTC")


def render_conflicts(conflicts: list[FileWriteConflict]) -> Table | str:
    """Render conflicts as a Rich table, or the empty-state string."""
    if not conflicts:
        return _EMPTY_STATE

    table = repl_table(
        title="Agent file-write conflicts",
        title_style=ui_theme.BOLD_BRAND,
    )
    table.add_column("path", style="bold", overflow="fold")
    table.add_column("agents", overflow="fold")
    table.add_column("first seen", style=ui_theme.DIM)
    table.add_column("last seen", style=ui_theme.DIM)

    for conflict in conflicts:
        table.add_row(
            escape(conflict.path),
            escape(", ".join(conflict.agents)),
            _format_timestamp(conflict.first_seen),
            _format_timestamp(conflict.last_seen),
        )
    return table


__all__ = ["render_conflicts"]
