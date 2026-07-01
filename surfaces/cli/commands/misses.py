"""``opensre misses`` — triage and export investigation misses as regressions.

Misses are written to ``~/.opensre/misses.jsonl`` by the post-investigation
feedback prompt whenever a user rates an outcome ``partial`` or ``inaccurate``.
These commands let humans and the weekly automation read that store, surface
recurrence trends, and convert the top offenders into benchmark scenarios that
the existing eval runner consumes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from core.domain.feedback import (
    MissTaxonomy,
    compute_stats,
    load_misses,
    misses_path,
    to_benchmark_scenario,
)
from core.domain.feedback.misses import (
    export_scenarios,
    filter_top_misses,
    parse_since,
)

_console = Console(highlight=False)

_TAXONOMY_VALUES = [t.value for t in MissTaxonomy]


def _parse_since_option(value: str | None) -> Any:
    if not value:
        return None
    try:
        return parse_since(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--since") from None


@click.group(name="misses")
def misses_command() -> None:
    """Investigation miss triage and eval scenario export."""


@misses_command.command(name="list")
@click.option(
    "--since",
    "since_spec",
    default=None,
    help="Only misses captured after this point. e.g. 7d, 24h, 2w, or an ISO timestamp.",
)
@click.option(
    "--taxonomy",
    type=click.Choice(_TAXONOMY_VALUES, case_sensitive=False),
    default=None,
    help="Filter by taxonomy bucket.",
)
@click.option("--json", "json_out", is_flag=True, help="Emit JSON instead of a table.")
@click.option("--limit", type=int, default=50, show_default=True, help="Max rows to render.")
def list_command(since_spec: str | None, taxonomy: str | None, json_out: bool, limit: int) -> None:
    """List recent misses."""
    since = _parse_since_option(since_spec)
    rows = load_misses(since=since, taxonomy=taxonomy)
    rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    rows = rows[:limit]

    if json_out:
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if not rows:
        _console.print(f"[dim]No misses recorded in scope. Store: {misses_path()}[/]")
        return

    table = Table(show_lines=False)
    table.add_column("when", style="dim")
    table.add_column("alert_name")
    table.add_column("taxonomy")
    table.add_column("rating")
    table.add_column("root cause", overflow="fold")

    for row in rows:
        table.add_row(
            (row.get("timestamp") or "")[:19],
            row.get("alert_name") or "<unknown>",
            row.get("taxonomy") or MissTaxonomy.UNKNOWN.value,
            row.get("rating") or "",
            (row.get("root_cause") or "")[:80],
        )

    _console.print(table)
    _console.print(f"[dim]{len(rows)} miss(es) shown · store: {misses_path()}[/]")


@misses_command.command(name="stats")
@click.option(
    "--since",
    "since_spec",
    default=None,
    help="Only misses captured after this point. e.g. 7d, 24h, 2w.",
)
@click.option("--json", "json_out", is_flag=True, help="Emit JSON instead of a table.")
def stats_command(since_spec: str | None, json_out: bool) -> None:
    """Show taxonomy breakdown and recurrence — the CLI dashboard."""
    since = _parse_since_option(since_spec)
    rows = load_misses(since=since)
    stats = compute_stats(rows)

    if json_out:
        click.echo(json.dumps(stats, indent=2, ensure_ascii=False, default=str))
        return

    _console.print(
        f"[bold]Closed-loop learning · {stats['total']} miss(es) across "
        f"{stats['unique_alerts']} alert(s)[/]"
    )

    by_taxonomy = Table(title="By taxonomy", title_justify="left")
    by_taxonomy.add_column("taxonomy")
    by_taxonomy.add_column("count", justify="right")
    for tax in _TAXONOMY_VALUES:
        by_taxonomy.add_row(tax, str(stats["by_taxonomy"].get(tax, 0)))
    _console.print(by_taxonomy)

    if stats["recurring"]:
        recurring = Table(title="Recurring misses (2+ occurrences)", title_justify="left")
        recurring.add_column("alert_name")
        recurring.add_column("taxonomy")
        recurring.add_column("count", justify="right")
        for alert, tax, count in stats["recurring"]:
            recurring.add_row(alert or "<unknown>", tax or "", str(count))
        _console.print(recurring)
    else:
        _console.print("[dim]No recurring (alert, taxonomy) pairs in scope.[/]")


@misses_command.command(name="export")
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Directory to write per-case alert.json files into.",
)
@click.option(
    "--since",
    "since_spec",
    default="7d",
    show_default=True,
    help="Only convert misses captured after this point.",
)
@click.option(
    "--top",
    type=int,
    default=10,
    show_default=True,
    help="Maximum scenarios to write (deduped by alert+taxonomy).",
)
@click.option(
    "--taxonomy",
    type=click.Choice(_TAXONOMY_VALUES, case_sensitive=False),
    default=None,
    help="Only export misses in this bucket.",
)
def export_command(out_dir: Path, since_spec: str, top: int, taxonomy: str | None) -> None:
    """Convert top misses into benchmark scenarios.

    Writes one ``alert.json`` per (alert, taxonomy) into ``out_dir`` using the
    same schema as benchmark scenario ``alert.json`` files so the
    existing benchmark runner can pick them up as regressions.
    """
    since = _parse_since_option(since_spec)
    rows = load_misses(since=since, taxonomy=taxonomy)

    if not rows:
        _console.print(
            f"[yellow]No misses in scope (since={since_spec}, taxonomy={taxonomy or 'any'}).[/]"
        )
        return

    selected = filter_top_misses(rows, top=top)
    written = export_scenarios(selected, out_dir=out_dir)

    _console.print(
        f"[green]Wrote {len(written)} scenario(s) to {out_dir}.[/] "
        f"[dim]({len(rows)} miss(es) in scope, deduped to {len(selected)})[/]"
    )
    for path in written:
        _console.print(f"  [dim]· {path}[/]")


@misses_command.command(name="convert")
@click.argument("miss_id")
@click.option(
    "--out",
    "out_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the scenario JSON to this path instead of stdout.",
)
def convert_command(miss_id: str, out_file: Path | None) -> None:
    """Convert a single miss (by id) into a benchmark scenario payload."""
    rows = load_misses()
    match = next((r for r in rows if r.get("miss_id") == miss_id), None)
    if match is None:
        raise click.ClickException(f"miss_id {miss_id!r} not found in {misses_path()}")

    scenario = to_benchmark_scenario(match)
    payload = json.dumps(scenario, indent=2, ensure_ascii=False) + "\n"

    if out_file is None:
        click.echo(payload)
        return

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(payload, encoding="utf-8")
    _console.print(f"[green]Wrote scenario to {out_file}.[/]")
