"""``opensre sentry digest`` — scheduled Sentry morning digest delivery.

Uses the headless sentry-summary skill path (not the investigation pipeline or
generic ``opensre cron`` kinds). Tasks are stored in the shared scheduler store
but are created and listed only through this command group.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from surfaces.cli.commands.cron import _validate_cron_and_timezone

_console = Console()


def _install_scheduler_runners() -> None:
    from integrations.harness_adapters import register_harness_adapters as register_integrations
    from integrations.sentry.scheduler_bootstrap import install as install_sentry_runner
    from tools.harness_adapters import register_harness_adapters as register_tools
    from tools.investigation.scheduler_bootstrap import install as install_investigation_runner

    register_integrations()
    register_tools()
    install_investigation_runner()
    install_sentry_runner()


@click.group(name="sentry")
def sentry_command() -> None:
    """Sentry-specific automation and digests."""


@sentry_command.group(name="digest")
def sentry_digest_command() -> None:
    """Run or schedule the Sentry morning digest (#10)."""


@sentry_digest_command.command(name="run")
@click.option(
    "--project",
    "project_slug",
    type=str,
    default="",
    help="Optional Sentry project slug to scope the digest.",
)
def sentry_digest_run(project_slug: str) -> None:
    """Run the morning digest once and print the report to stdout."""
    from platform.scheduler.agent_runner import invoke_agent_runner

    _install_scheduler_runners()
    payload: dict[str, str] = {
        "source": "cli_sentry_morning_digest",
        "stats_period": "24h",
        "query": "is:unresolved",
    }
    if project_slug.strip():
        payload["project_slug"] = project_slug.strip()

    try:
        message = invoke_agent_runner(payload)
    except Exception as exc:
        _console.print(f"[red]Sentry morning digest failed: {exc}[/red]")
        raise SystemExit(1) from exc

    _console.print(message)


@sentry_digest_command.group(name="schedule")
def sentry_digest_schedule_command() -> None:
    """Manage scheduled Sentry morning digest deliveries."""


@sentry_digest_schedule_command.command(name="add")
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
    type=click.Choice(["telegram", "slack"], case_sensitive=False),
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
    "--project",
    "project_slug",
    type=str,
    default="",
    help="Optional Sentry project slug to scope the digest.",
)
def sentry_digest_schedule_add(
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    project_slug: str,
) -> None:
    """Schedule daily Sentry morning digest delivery."""
    from integrations.sentry.digest_prerequisites import (
        require_digest_delivery_provider,
        require_sentry_integration,
    )
    from platform.scheduler.store import add_task
    from platform.scheduler.types import Provider, ScheduledTask, TaskKind

    require_sentry_integration()
    require_digest_delivery_provider(provider)
    _validate_cron_and_timezone(cron_expr, timezone)

    params: dict[str, str] = {}
    if project_slug.strip():
        params["project_slug"] = project_slug.strip()

    task = ScheduledTask(
        kind=TaskKind.SENTRY_MORNING_DIGEST,
        cron=cron_expr,
        timezone=timezone,
        provider=Provider(provider),
        chat_id=chat_id,
        window_hours=24,
        params=params,
    )
    added = add_task(task)
    _console.print(f"[green]Sentry digest task {added.id} created.[/green]")
    _console.print(f"  Cron: {added.cron}  TZ: {added.timezone}")
    _console.print(f"  Provider: {added.provider.value}  Chat: {added.chat_id}")
    if params:
        _console.print(f"  Project: {params['project_slug']}")


@sentry_digest_schedule_command.command(name="list")
def sentry_digest_schedule_list() -> None:
    """List scheduled Sentry morning digest tasks."""
    from platform.scheduler.store import list_tasks
    from platform.scheduler.types import TaskKind

    tasks = [task for task in list_tasks() if task.kind == TaskKind.SENTRY_MORNING_DIGEST]
    if not tasks:
        _console.print("[dim]No Sentry morning digest schedules configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Cron")
    table.add_column("TZ")
    table.add_column("Provider")
    table.add_column("Chat")
    table.add_column("Project")
    table.add_column("Enabled")
    table.add_column("Last run")

    for task in tasks:
        project = task.params.get("project_slug", "—")
        table.add_row(
            task.display_id(),
            task.cron,
            task.timezone,
            task.provider.value,
            task.chat_id,
            project or "—",
            "✓" if task.enabled else "✗",
            task.last_run or "—",
        )

    _console.print(table)


@sentry_digest_schedule_command.command(name="remove")
@click.argument("task_id")
def sentry_digest_schedule_remove(task_id: str) -> None:
    """Remove a scheduled Sentry morning digest task."""
    from platform.scheduler.store import get_task, remove_task
    from platform.scheduler.types import TaskKind

    task = get_task(task_id)
    if task is None or task.kind != TaskKind.SENTRY_MORNING_DIGEST:
        _console.print(f"[red]Error: Sentry digest task {task_id} not found.[/red]")
        raise SystemExit(1)

    if remove_task(task_id):
        _console.print(f"[green]Task {task_id} removed.[/green]")
    else:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)


@sentry_digest_schedule_command.command(name="run")
@click.argument("task_id")
def sentry_digest_schedule_run(task_id: str) -> None:
    """Run a scheduled Sentry digest task immediately."""
    from integrations.sentry.digest_prerequisites import require_digest_delivery_provider
    from platform.scheduler.runner import run_task_now
    from platform.scheduler.store import get_task
    from platform.scheduler.types import TaskKind

    _install_scheduler_runners()
    task = get_task(task_id)
    if task is None or task.kind != TaskKind.SENTRY_MORNING_DIGEST:
        _console.print(f"[red]Error: Sentry digest task {task_id} not found.[/red]")
        raise SystemExit(1)

    require_digest_delivery_provider(task.provider.value)

    _console.print(f"Running Sentry digest task {task_id}...")
    success = run_task_now(task_id)
    if success:
        _console.print("[green]Done.[/green]")
    else:
        _console.print("[red]Task execution failed. Check logs for details.[/red]")
        raise SystemExit(1)


__all__ = ["sentry_command"]
