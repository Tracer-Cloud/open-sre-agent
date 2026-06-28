"""Slash command /rca — browse persisted RCA reports across sessions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from interactive_shell.command_registry.investigation import (
    render_investigation_report,
    write_investigation_export,
)
from interactive_shell.command_registry.types import SlashCommand
from interactive_shell.runtime import ReplSession
from interactive_shell.session import default_session_repo
from interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    print_repl_table,
    repl_table,
)
from interactive_shell.ui.components.choice_menu import (
    CRUMB_SEP,
    prepare_repl_output_line,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from interactive_shell.ui.components.time_format import format_repl_timestamp
from interactive_shell.utils.error_handling.exception_reporting import report_exception

_RCA_ROOT = "/rca"
_RCA_LATEST = "__latest__"
_RCA_HISTORY = "__history__"
_RCA_SAVE = "__save__"
_HISTORY_ALIASES = frozenset({"history", "list", "ls"})
_EXPORT_SUFFIXES = frozenset({".md", ".json"})


def _investigation_id(record: dict[str, object]) -> str:
    return str(record.get("investigation_id") or "")


def _record_timestamp(record: dict[str, object], *, style: str) -> str:
    return format_repl_timestamp(record.get("completed_at"), style=style)  # type: ignore[arg-type]


def _rca_breadcrumb(suffix: str) -> str:
    return _RCA_ROOT if not suffix else f"{_RCA_ROOT}{CRUMB_SEP}{suffix}"


def _rca_record_label(record: dict[str, object]) -> str:
    inv_id = _investigation_id(record) or "—"
    completed = _record_timestamp(record, style="compact")
    preview = str(record.get("root_cause_preview") or "—")
    if len(preview) > 44:
        preview = preview[:41] + "…"
    trigger = str(record.get("trigger") or "").strip()
    trigger_part = f"  {trigger[:28]}" if trigger else ""
    return f"{inv_id[:8]}  {completed}  {preview}{trigger_part}"


def _print_rca_empty(console: Console) -> None:
    console.print(f"[{DIM}]no persisted RCA reports yet.[/]")
    console.print(
        f"[{DIM}]run an investigation with[/] [{WARNING}]/investigate[/] "
        f"[{DIM}]to populate history.[/]"
    )


def _require_rca_records(console: Console) -> list[dict[str, object]] | None:
    records = default_session_repo().load_investigation_history()
    if not records:
        _print_rca_empty(console)
        return None
    return records


def _rca_record_export_state(record: dict[str, object]) -> dict[str, object]:
    report = str(record.get("report") or "")
    return {
        "investigation_id": record.get("investigation_id"),
        "session_id": record.get("session_id"),
        "completed_at": record.get("completed_at"),
        "trigger": record.get("trigger"),
        "root_cause": record.get("root_cause"),
        "problem_md": report,
        "report": report,
        "root_cause_category": record.get("root_cause_category"),
        "alert_name": record.get("alert_name"),
        "run_id": record.get("run_id"),
    }


def _print_rca_lookup_failure(
    console: Console,
    investigation_id: str,
    *,
    match_count: int,
) -> None:
    if match_count > 1:
        console.print(
            f"[{WARNING}]ambiguous id prefix:[/] {escape(investigation_id)} "
            f"[{DIM}]({match_count} matches — use more characters)[/]"
        )
        return
    console.print(f"[{ERROR}]RCA report not found:[/] {escape(investigation_id)}")


def _resolve_rca_record(
    investigation_id: str | None,
    *,
    records: list[dict[str, object]] | None = None,
) -> dict[str, object] | None:
    repo = default_session_repo()
    if investigation_id:
        loaded = repo.load_investigation(investigation_id)
        if loaded is not None:
            return loaded
        if records:
            for record in records:
                inv_id = _investigation_id(record)
                if inv_id.startswith(investigation_id):
                    return record
        return None

    history = records or repo.load_investigation_history()
    if not history:
        return None
    latest = history[0]
    inv_id = _investigation_id(latest)
    if not inv_id:
        return latest
    return repo.load_investigation(inv_id) or latest


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _normalize_rca_save_path(raw_path: str, *, investigation_id: str = "") -> Path:
    """Normalize user-entered save paths (strip quotes, expand ~, folder → file)."""
    value = _strip_outer_quotes(raw_path.strip())
    treat_as_dir = value.endswith(("/", "\\"))
    dest = Path(value).expanduser()
    if dest.suffix.lower() not in _EXPORT_SUFFIXES and (treat_as_dir or dest.is_dir()):
        dest = dest / f"rca-{investigation_id[:8] or 'report'}.md"
    return dest


def _save_rca_record(console: Console, record: dict[str, object], dest_path: str) -> bool:
    inv_id = _investigation_id(record)
    dest = _normalize_rca_save_path(dest_path, investigation_id=inv_id)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_investigation_export(
            dest,
            root_cause=str(record.get("root_cause") or ""),
            report=str(record.get("report") or ""),
            full_state=_rca_record_export_state(record),
        )
        console.print(f"[{HIGHLIGHT}]saved:[/] {escape(str(dest))}")
    except IsADirectoryError:
        console.print(
            f"[{ERROR}]save failed:[/] {escape(str(dest))} is a directory — "
            f"include a filename (e.g. [{WARNING}]report.md[/])"
        )
    except Exception as exc:
        report_exception(exc, context="interactive_shell.rca_save")
        console.print(f"[{ERROR}]save failed:[/] {escape(str(exc))}")
    return True


def _prompt_rca_save_path(console: Console) -> str | None:
    console.print()
    console.print(
        f"[{DIM}]Enter output file or folder (.md or .json). "
        f"Example:[/] [{WARNING}]rca-report.md[/] "
        f"[{DIM}]or[/] [{WARNING}]/Users/you/Downloads/rca reports/[/]"
    )
    try:
        value = console.input(f"[{HIGHLIGHT}]file path> [/]").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return value or None


def _report_picker_choices(
    records: list[dict[str, object]],
    *,
    include_latest: bool,
) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    if include_latest:
        choices.append((_RCA_LATEST, "latest"))
    choices.extend(
        (inv_id, _rca_record_label(record))
        for record in records
        if (inv_id := _investigation_id(record))
    )
    choices.append(("done", "done"))
    return choices


def _pick_rca_report(
    records: list[dict[str, object]],
    *,
    breadcrumb_suffix: str,
    include_latest: bool = False,
) -> str | None:
    picked = repl_choose_one(
        title="rca report",
        breadcrumb=_rca_breadcrumb(breadcrumb_suffix),
        choices=_report_picker_choices(records, include_latest=include_latest),
    )
    if picked is None or picked == "done":
        return None
    return picked


def _picked_investigation_id(picked: str, records: list[dict[str, object]]) -> str:
    if picked == _RCA_LATEST:
        return _investigation_id(records[0])
    return picked


def _interactive_rca_report_menu(
    session: ReplSession,
    console: Console,
    *,
    breadcrumb_suffix: str,
    include_latest: bool,
    on_pick: Callable[[ReplSession, Console, dict[str, object]], bool],
) -> bool:
    records = _require_rca_records(console)
    if records is None:
        return True

    picked = _pick_rca_report(
        records,
        breadcrumb_suffix=breadcrumb_suffix,
        include_latest=include_latest,
    )
    if picked is None:
        return True

    record = _resolve_rca_record(_picked_investigation_id(picked, records), records=records)
    if record is None:
        console.print(f"[{DIM}]RCA report not found.[/]")
        return True
    return on_pick(session, console, record)


def _interactive_show_record(
    session: ReplSession,
    console: Console,
    record: dict[str, object],
) -> bool:
    inv_id = _investigation_id(record)
    if not inv_id:
        return True
    _cmd_rca_show(session, console, inv_id, record=record)
    repl_section_break(console)
    return True


def _interactive_save_record(
    _session: ReplSession,
    console: Console,
    record: dict[str, object],
) -> bool:
    dest_path = _prompt_rca_save_path(console)
    if dest_path is None:
        return True
    return _save_rca_record(console, record, dest_path)


def _interactive_rca_history_menu(session: ReplSession, console: Console) -> bool:
    return _interactive_rca_report_menu(
        session,
        console,
        breadcrumb_suffix="history",
        include_latest=False,
        on_pick=_interactive_show_record,
    )


def _interactive_rca_save_menu(session: ReplSession, console: Console) -> bool:
    return _interactive_rca_report_menu(
        session,
        console,
        breadcrumb_suffix="save",
        include_latest=True,
        on_pick=_interactive_save_record,
    )


def _interactive_rca_root_menu(session: ReplSession, console: Console) -> bool:
    records = _require_rca_records(console)
    if records is None:
        return True

    picked = repl_choose_one(
        title="rca report",
        breadcrumb=_RCA_ROOT,
        choices=[
            (_RCA_LATEST, "latest"),
            (_RCA_HISTORY, "history"),
            (_RCA_SAVE, "save"),
            ("done", "done"),
        ],
    )
    if picked is None or picked == "done":
        return True
    if picked == _RCA_HISTORY:
        return _interactive_rca_history_menu(session, console)
    if picked == _RCA_SAVE:
        return _interactive_rca_save_menu(session, console)

    latest_id = _investigation_id(records[0])
    if not latest_id:
        return True
    return _interactive_show_record(session, console, records[0])


def _cmd_rca_history(_session: ReplSession, console: Console) -> bool:
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


def _print_rca_record_header(console: Console, record: dict[str, object]) -> None:
    console.print()
    console.print(
        f"[{DIM}]id[/] [bold]{escape(_investigation_id(record))}[/]  "
        f"[{DIM}]session[/] {escape(str(record.get('session_id') or '')[:8])}  "
        f"[{DIM}]completed[/] {escape(_record_timestamp(record, style='table'))}"
    )
    trigger = str(record.get("trigger") or "").strip()
    if trigger:
        console.print(f"[{DIM}]trigger[/] {escape(trigger)}")


def _cmd_rca_show(
    _session: ReplSession,
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

    _print_rca_record_header(console, resolved)
    render_investigation_report(
        console,
        root_cause=str(resolved.get("root_cause") or ""),
        report=str(resolved.get("report") or ""),
    )
    return True


def _cmd_rca_save(
    _session: ReplSession,
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


def _cmd_rca(_session: ReplSession, console: Console, args: list[str]) -> bool:
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
