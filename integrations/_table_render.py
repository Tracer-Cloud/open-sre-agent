"""Shared Rich-table rendering for the integrations CLI surface.

``integrations/cli.py`` (``cmd_list``) and ``integrations/verify.py``
(``format_verification_results``) each render a themed table to plain text
for terminal/JSON-adjacent output. Both need the same box style and the same
record-then-export-plain-text Console dance, so it lives here once instead
of being copied per caller.
"""

from __future__ import annotations

import io

from rich import box
from rich.console import Console
from rich.table import Table

from platform.terminal.theme import TEXT


def new_table() -> Table:
    """Return a ``Table`` pre-configured with the shared integrations CLI style."""
    return Table(
        box=box.MINIMAL_HEAVY_HEAD,
        show_edge=False,
        pad_edge=False,
        header_style=f"bold {TEXT}",
    )


def render_table(table: Table) -> str:
    """Render *table* to plain text (no ANSI) safe to ``print()`` or capture.

    ``styles=False`` keeps the returned string parseable by anything that
    captures it (redirected output, scripts matching on ``passed``/``failed``);
    only the live terminal write via ``force_terminal=True`` gets color.
    """
    console = Console(
        file=io.StringIO(),
        record=True,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    console.print()
    console.print(table)
    console.print()
    return console.export_text(styles=False)


__all__ = ["new_table", "render_table"]
