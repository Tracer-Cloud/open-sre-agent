"""Finding, resolving, and formatting stored RCA record metadata."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from core.agent_harness.spi.defaults import default_session_repo
from surfaces.interactive_shell.command_registry.investigation import (
    render_investigation_report,
)
from surfaces.interactive_shell.ui import DIM, ERROR, WARNING
from surfaces.shared.terminal.components.time_format import format_repl_timestamp


def _investigation_id(record: dict[str, object]) -> str:
    return str(record.get("investigation_id") or "")


def _record_timestamp(record: dict[str, object], *, style: str) -> str:
    return format_repl_timestamp(record.get("completed_at"), style=style)  # type: ignore[arg-type]


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


def _display_rca_record(console: Console, record: dict[str, object]) -> None:
    _print_rca_record_header(console, record)
    render_investigation_report(
        console,
        root_cause=str(record.get("root_cause") or ""),
        report=str(record.get("report") or ""),
    )
