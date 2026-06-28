"""Session listing slash command: /sessions."""

from __future__ import annotations

import contextlib

from rich.console import Console
from rich.markup import escape

from interactive_shell.runtime import ReplSession
from interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    print_repl_table,
    repl_table,
)
from interactive_shell.ui.components.time_format import format_repl_duration, format_repl_timestamp


def _cmd_sessions(session: ReplSession, console: Console, _args: list[str]) -> bool:
    from datetime import UTC, datetime

    from interactive_shell.session import default_session_repo

    entries = default_session_repo().load_recent(20)
    if not entries:
        console.print(f"[{DIM}]No sessions recorded yet.[/]")
        return True

    table = repl_table(title="Recent sessions\n", title_style=BOLD_BRAND)
    table.add_column("#", style="bold", justify="right")
    table.add_column("Session ID", style="bold")
    table.add_column("Name")
    table.add_column("Started")
    table.add_column("Duration")
    table.add_column("Turns", justify="right")
    table.add_column("Investigations", justify="right")

    for i, entry in enumerate(entries, start=1):
        sid = entry["session_id"]
        short_id = sid[:8] if len(sid) >= 8 else sid
        is_current = sid == session.session_id

        name = entry.get("name") or ""
        if is_current and not name and session.resumed_from_name:
            name = f"↩ {session.resumed_from_name}"
        if is_current:
            name_col = f"[{DIM}](current)[/]" if not name else f"{escape(name)} [{DIM}](current)[/]"
        else:
            name_col = escape(name) if name else f"[{DIM}]—[/]"

        started_str = format_repl_timestamp(entry.get("started_at"), style="table")

        duration_secs = entry.get("duration_secs")
        if is_current:
            with contextlib.suppress(OSError, OverflowError, ValueError):
                elapsed = int(
                    (
                        datetime.now(UTC) - datetime.fromtimestamp(session.started_at, tz=UTC)
                    ).total_seconds()
                )
                duration_secs = elapsed

        total = entry.get("total_turns")
        investigations = entry.get("investigation_turns")

        table.add_row(
            str(i),
            short_id,
            name_col,
            started_str,
            format_repl_duration(duration_secs),
            str(total) if total is not None else "—",
            str(investigations) if investigations is not None else "—",
        )

    print_repl_table(console, table)
    return True
