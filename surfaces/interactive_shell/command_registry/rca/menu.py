"""Interactive TTY menus and pickers for persisted RCA reports."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from surfaces.interactive_shell.command_registry.rca.export import (
    _prompt_rca_save_path,
    _save_rca_record,
)
from surfaces.interactive_shell.command_registry.rca.records import (
    _display_rca_record,
    _investigation_id,
    _rca_record_label,
    _require_rca_records,
    _resolve_rca_record,
)
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM
from surfaces.shared.terminal.components.choice_menu import (
    CRUMB_SEP,
    repl_choose_one,
    repl_section_break,
)

_RCA_ROOT = "/rca"
_RCA_LATEST = "__latest__"
_RCA_HISTORY = "__history__"
_RCA_SAVE = "__save__"


def _rca_breadcrumb(suffix: str) -> str:
    return _RCA_ROOT if not suffix else f"{_RCA_ROOT}{CRUMB_SEP}{suffix}"


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
    session: Session,
    console: Console,
    *,
    breadcrumb_suffix: str,
    include_latest: bool,
    on_pick: Callable[[Session, Console, dict[str, object]], bool],
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
    _session: Session,
    console: Console,
    record: dict[str, object],
) -> bool:
    inv_id = _investigation_id(record)
    if not inv_id:
        return True
    _display_rca_record(console, record)
    repl_section_break(console)
    return True


def _interactive_save_record(
    _session: Session,
    console: Console,
    record: dict[str, object],
) -> bool:
    dest_path = _prompt_rca_save_path(console)
    if dest_path is None:
        return True
    return _save_rca_record(console, record, dest_path)


def _interactive_rca_history_menu(session: Session, console: Console) -> bool:
    return _interactive_rca_report_menu(
        session,
        console,
        breadcrumb_suffix="history",
        include_latest=False,
        on_pick=_interactive_show_record,
    )


def _interactive_rca_save_menu(session: Session, console: Console) -> bool:
    return _interactive_rca_report_menu(
        session,
        console,
        breadcrumb_suffix="save",
        include_latest=True,
        on_pick=_interactive_save_record,
    )


def _interactive_rca_root_menu(session: Session, console: Console) -> bool:
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
