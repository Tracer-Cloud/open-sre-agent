"""``opensre cron`` command group: manage scheduled deliveries.

Provides CLI surface for creating, listing, removing, running, and
viewing logs of cron-driven scheduled tasks that deliver reports to
messaging providers.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

_console = Console()


@click.group(name="cron")
def cron_command() -> None:
    """Manage cron-driven scheduled deliveries to messaging providers."""


@cron_command.command(name="add")
@click.option(
    "--kind",
    type=click.Choice(
        [
            "daily_summary",
            "weekly_audit",
            "incident_window_replay",
            "synthetic_run",
            "custom_investigation",
        ],
        case_sensitive=False,
    ),
    required=True,
    help="The kind of scheduled task.",
)
@click.option(
    "--cron",
    "cron_expr",
    type=str,
    required=True,
    help="Cron expression (5 fields: minute hour day month day_of_week).",
)
@click.option(
    "--tz",
    "timezone",
    type=str,
    default="UTC",
    show_default=True,
    help="IANA timezone for the schedule (e.g. Europe/London, US/Eastern).",
)
@click.option(
    "--provider",
    type=click.Choice(["telegram", "slack", "discord"], case_sensitive=False),
    required=True,
    help="Messaging provider for delivery.",
)
@click.option(
    "--chat-id",
    type=str,
    required=True,
    help="Chat/channel ID for the target provider.",
)
@click.option(
    "--window",
    "window_hours",
    type=click.IntRange(min=1),
    default=24,
    show_default=True,
    help="Lookback window in hours for the report (must be >= 1).",
)
def cron_add(
    kind: str,
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    window_hours: int,
) -> None:
    """Add a new scheduled delivery task."""
    from platform.scheduler.types import Provider, ScheduledTask, TaskKind

    # Validate cron expression by constructing the APScheduler trigger
    _validate_cron_and_timezone(cron_expr, timezone)

    task = ScheduledTask(
        kind=TaskKind(kind),
        cron=cron_expr,
        timezone=timezone,
        provider=Provider(provider),
        chat_id=chat_id,
        window_hours=window_hours,
    )

    from platform.scheduler.store import add_task

    added = add_task(task)
    _console.print(f"[green]Task {added.id} created.[/green]")
    _console.print(f"  Kind: {added.kind.value}  Cron: {added.cron}  TZ: {added.timezone}")
    _console.print(f"  Provider: {added.provider.value}  Chat: {added.chat_id}")


@cron_command.command(name="list")
def cron_list() -> None:
    """List all scheduled delivery tasks."""
    from platform.scheduler.store import list_tasks

    tasks = list_tasks()
    if not tasks:
        _console.print("[dim]No scheduled tasks configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Kind")
    table.add_column("Cron")
    table.add_column("TZ")
    table.add_column("Provider")
    table.add_column("Enabled")
    table.add_column("Last Run")

    for task in tasks:
        table.add_row(
            task.display_id(),
            task.kind.value,
            task.cron,
            task.timezone,
            task.provider.value,
            "✓" if task.enabled else "✗",
            task.last_run or "—",
        )

    _console.print(table)


@cron_command.command(name="remove")
@click.argument("task_id")
def cron_remove(task_id: str) -> None:
    """Remove a scheduled delivery task by ID."""
    from platform.scheduler.store import remove_task

    if remove_task(task_id):
        _console.print(f"[green]Task {task_id} removed.[/green]")
    else:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)


@cron_command.command(name="run")
@click.argument("task_id")
def cron_run(task_id: str) -> None:
    """Run a scheduled task immediately (ad-hoc one-shot for debugging)."""
    from platform.scheduler.runner import run_task_now
    from platform.scheduler.store import get_task

    task = get_task(task_id)
    if task is None:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)

    _console.print(f"Running task {task_id} ({task.kind.value})...")
    success = run_task_now(task_id)
    if success:
        _console.print("[green]Done.[/green]")
    else:
        _console.print("[red]Task execution failed. Check logs for details.[/red]")
        raise SystemExit(1)


@cron_command.command(name="logs")
@click.argument("task_id")
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=20,
    show_default=True,
    help="Max number of runs to show (must be >= 1).",
)
def cron_logs(task_id: str, limit: int) -> None:
    """Show execution history for a scheduled task."""
    from platform.scheduler.claim_store import get_runs
    from platform.scheduler.store import get_task

    task = get_task(task_id)
    if task is None:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)

    runs = get_runs(task_id, limit=limit)
    if not runs:
        _console.print(f"[dim]No execution history for task {task_id}.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Started")
    table.add_column("Status")
    table.add_column("Message ID")
    table.add_column("Error")

    for run in runs:
        status_style = (
            "green"
            if run.status.value == "success"
            else "red"
            if run.status.value == "failed"
            else ""
        )
        table.add_row(
            run.started_at,
            f"[{status_style}]{run.status.value}[/{status_style}]"
            if status_style
            else run.status.value,
            run.posted_message_id or "—",
            run.error[:50] if run.error else "—",
        )

    _console.print(table)


@cron_command.command(name="start")
def cron_start() -> None:
    """Start the scheduler daemon (blocks until interrupted)."""
    from platform.scheduler.runner import start_scheduler

    _console.print("[bold]Starting scheduler daemon...[/bold]")
    _console.print("Press Ctrl+C to stop.")
    start_scheduler()


def _validate_cron_and_timezone(cron_expr: str, timezone: str) -> None:
    """Validate cron expression and timezone by constructing an APScheduler trigger.

    Fails fast with a clear error message instead of creating inert tasks.
    """
    parts = cron_expr.split()
    if len(parts) != 5:
        _console.print("[red]Error: cron expression must have exactly 5 fields.[/red]")
        _console.print("  Format: minute hour day month day_of_week")
        _console.print("  Example: 0 9 * * 1-5  (weekdays at 09:00)")
        raise SystemExit(1)

    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(cron_expr, timezone=timezone)
    except (ValueError, TypeError, KeyError) as exc:
        _console.print(f"[red]Error: invalid cron expression or timezone: {exc}[/red]")
        raise SystemExit(1) from exc


__all__ = ["cron_command"]
