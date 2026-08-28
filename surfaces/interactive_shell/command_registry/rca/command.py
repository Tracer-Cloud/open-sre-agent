"""Slash command handlers and registration for /rca."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from core.agent_harness.spi.defaults import default_session_repo
from surfaces.interactive_shell.command_registry.rca.export import _save_rca_record
from surfaces.interactive_shell.command_registry.rca.menu import (
    _interactive_rca_history_menu,
    _interactive_rca_root_menu,
    _interactive_rca_save_menu,
)
from surfaces.interactive_shell.command_registry.rca.records import (
    _display_rca_record,
    _investigation_id,
    _print_rca_empty,
    _print_rca_lookup_failure,
    _record_timestamp,
    _require_rca_records,
    _resolve_rca_record,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    WARNING,
    print_repl_table,
    repl_table,
)
from surfaces.shared.terminal.components.choice_menu import (
    prepare_repl_output_line,
    repl_tty_interactive,
)

_HISTORY_ALIASES = frozenset({"history", "list", "ls"})


def _cmd_rca_history(_session: Session, console: Console) -> bool:
    records = _require_rca_records(console)
    if records is None:
        return True

    table = repl_table(title="RCA history\n", title_style=BOLD_BRAND)
    table.add_column("#", style="bold", justify="right")
    table.add_column("ID", style="bold")
    table.add_column("Completed")
    table.add_column("Trigger", overflow="fold")
    table.add_column("Root cause", overflow="fold", style=DIM)

    for index, record in enumerate(records, start=1):
        table.add_row(
            str(index),
            _investigation_id(record) or "—",
            _record_timestamp(record, style="table"),
            escape(str(record.get("trigger") or record.get("session_name") or "—")),
            escape(str(record.get("root_cause_preview") or "—")),
        )

    print_repl_table(console, table)
    console.print(
        f"[{DIM}]show full report:[/] [{WARNING}]/rca show <id>[/]  "
        f"[{DIM}]save:[/] [{WARNING}]/rca save <path>[/] "
        f"[{DIM}]or[/] [{WARNING}]/rca save <id> <path>[/]"
    )
    return True


def _cmd_rca_show(
    _session: Session,
    console: Console,
    investigation_id: str,
    *,
    record: dict[str, object] | None = None,
) -> bool:
    if record is not None:
        resolved = record
    else:
        loaded, match_count = default_session_repo().lookup_investigation(investigation_id)
        if match_count != 1:
            _print_rca_lookup_failure(console, investigation_id, match_count=match_count)
            return True
        if loaded is None:
            _print_rca_lookup_failure(console, investigation_id, match_count=0)
            return True
        resolved = loaded

    _display_rca_record(console, resolved)
    return True


def _cmd_rca_save(
    _session: Session,
    console: Console,
    *,
    investigation_id: str | None,
    dest_path: str,
) -> bool:
    if investigation_id:
        record, match_count = default_session_repo().lookup_investigation(investigation_id)
        if match_count != 1:
            _print_rca_lookup_failure(console, investigation_id, match_count=match_count)
            return True
        if record is None:
            _print_rca_lookup_failure(console, investigation_id, match_count=0)
            return True
    else:
        record = _resolve_rca_record(None)
        if record is None:
            _print_rca_empty(console)
            return True
    return _save_rca_record(console, record, dest_path)


def _cmd_rca(_session: Session, console: Console, args: list[str]) -> bool:
    prepare_repl_output_line()
    if not args:
        if repl_tty_interactive():
            return _interactive_rca_root_menu(_session, console)
        return _cmd_rca_history(_session, console)

    sub = args[0].lower().strip()
    if sub in _HISTORY_ALIASES:
        if repl_tty_interactive():
            return _interactive_rca_history_menu(_session, console)
        return _cmd_rca_history(_session, console)
    if sub == "show":
        if len(args) < 2:
            if repl_tty_interactive():
                return _interactive_rca_root_menu(_session, console)
            console.print(f"[{DIM}]usage:[/] /rca show <investigation-id-prefix>")
            return True
        return _cmd_rca_show(_session, console, args[1])
    if sub == "save":
        if len(args) == 1:
            if repl_tty_interactive():
                return _interactive_rca_save_menu(_session, console)
            console.print(
                f"[{DIM}]usage:[/] /rca save <path>  "
                f"[{DIM}]or[/] /rca save <investigation-id> <path>"
            )
            return True
        if len(args) == 2:
            return _cmd_rca_save(_session, console, investigation_id=None, dest_path=args[1])
        return _cmd_rca_save(_session, console, investigation_id=args[1], dest_path=args[2])

    console.print(
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        f"(try [bold]/rca history[/bold], [bold]/rca show <id>[/bold], "
        f"or [bold]/rca save <path>[/bold])"
    )
    return True


_RCA_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("history", "list persisted RCA reports across sessions"),
    ("show", "show one RCA report by investigation id"),
    ("save", "save an RCA report to a file (.md or .json)"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/rca",
        "Browse persisted RCA investigation reports.",
        _cmd_rca,
        usage=(
            "/rca",
            "/rca history",
            "/rca show <investigation-id>",
            "/rca save <path>",
            "/rca save <investigation-id> <path>",
        ),
        first_arg_completions=_RCA_FIRST_ARGS,
    ),
]

__all__ = ["COMMANDS"]
