"""Path normalization and file export for RCA investigation reports."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape

from surfaces.interactive_shell.command_registry.investigation import (
    write_investigation_export,
)
from surfaces.interactive_shell.command_registry.rca.records import _investigation_id
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING
from surfaces.shared.error_handling.exception_reporting import report_exception

_EXPORT_SUFFIXES = frozenset({".md", ".json"})


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
        report_exception(exc, context="surfaces.interactive_shell.rca_save")
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
