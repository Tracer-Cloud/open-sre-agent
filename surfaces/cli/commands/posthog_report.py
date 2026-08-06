"""``opensre posthog report`` — PostHog per-metric summary reports (#3824).

Uses the headless posthog-summary skill path (not the investigation pipeline or
generic ``opensre cron`` kinds). Tasks are stored in the shared scheduler store
but are created and listed only through this command group.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from platform.scheduler.delivery import SUPPORTED_DELIVERY_PROVIDERS
from surfaces.cli.commands.cron import _validate_cron_and_timezone
from surfaces.shared.runtime_bootstrap import install_runtime

_console = Console()
_PROVIDER_CHOICES = [p.value for p in SUPPORTED_DELIVERY_PROVIDERS]


@click.group(name="posthog")
def posthog_command() -> None:
    """PostHog-specific automation and reports."""


@posthog_command.group(name="report")
def posthog_report_command() -> None:
    """Run or schedule the PostHog per-metric summary report (#3824)."""


@posthog_report_command.command(name="run")
@click.option(
    "--period",
    "stats_period",
    type=str,
    default="7d",
    show_default=True,
    help="Lookback window for the report (e.g. 24h, 7d, 30d).",
)
@click.option(
    "--metrics",
    "metrics",
    type=str,
    default="",
    help="Optional comma-separated metrics to focus on (default: standard set).",
)
def posthog_report_run(stats_period: str, metrics: str) -> None:
    """Run the metric report once and print it to stdout."""
    from integrations.posthog.report_prerequisites import (
        DEFAULT_POSTHOG_PERIOD,
        require_posthog_integration,
    )
    from platform.scheduler.agent_runner import invoke_agent_runner

    require_posthog_integration()
    install_runtime()
    payload: dict[str, str] = {
        "source": "cli_posthog_metric_report",
        "stats_period": stats_period.strip() or DEFAULT_POSTHOG_PERIOD,
    }
    if metrics.strip():
        payload["metrics"] = metrics.strip()

    try:
        message = invoke_agent_runner(payload)
    except Exception as exc:
        _console.print(f"[red]PostHog report failed: {exc}[/red]")
        raise SystemExit(1) from exc

    _console.print(message)


@posthog_report_command.group(name="schedule")
def posthog_report_schedule_command() -> None:
    """Manage scheduled PostHog metric report deliveries."""


@posthog_report_schedule_command.command(name="add")
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
    type=click.Choice(_PROVIDER_CHOICES, case_sensitive=False),
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
    "--period",
    "stats_period",
    type=str,
    default="7d",
    show_default=True,
    help="Lookback window for the report (e.g. 24h, 7d, 30d).",
)
@click.option(
    "--metrics",
    "metrics",
    type=str,
    default="",
    help="Optional comma-separated metrics to focus on (default: standard set).",
)
def posthog_report_schedule_add(
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    stats_period: str,
    metrics: str,
) -> None:
    """Schedule recurring PostHog metric report delivery."""
    from integrations.posthog.report_prerequisites import (
        DEFAULT_POSTHOG_PERIOD,
        require_posthog_integration,
        require_report_delivery_provider,
    )
    from platform.scheduler.store import add_task
    from platform.scheduler.types import Provider, ScheduledTask, TaskKind

    require_posthog_integration()
    require_report_delivery_provider(provider)
    _validate_cron_and_timezone(cron_expr, timezone)

    period = stats_period.strip() or DEFAULT_POSTHOG_PERIOD
    params: dict[str, str] = {"stats_period": period}
    if metrics.strip():
        params["metrics"] = metrics.strip()

    task = ScheduledTask(
        kind=TaskKind.POSTHOG_METRIC_REPORT,
        cron=cron_expr,
        timezone=timezone,
        provider=Provider(provider),
        chat_id=chat_id,
        # The report runner reads stats_period from params, not window_hours;
        # keep 0 so operators aren't misled into thinking a lookback-hours
        # window applies here.
        window_hours=0,
        params=params,
    )
    added = add_task(task)
    _console.print(f"[green]PostHog report task {added.id} created.[/green]")
    _console.print(f"  Cron: {added.cron}  TZ: {added.timezone}")
    _console.print(f"  Provider: {added.provider.value}  Chat: {added.chat_id}")
    _console.print(f"  Period: {params['stats_period']}")
    if "metrics" in params:
        _console.print(f"  Metrics: {params['metrics']}")


@posthog_report_schedule_command.command(name="list")
def posthog_report_schedule_list() -> None:
    """List scheduled PostHog metric report tasks."""
    from platform.scheduler.store import list_tasks
    from platform.scheduler.types import TaskKind

    tasks = [task for task in list_tasks() if task.kind == TaskKind.POSTHOG_METRIC_REPORT]
    if not tasks:
        _console.print("[dim]No PostHog metric report schedules configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Cron")
    table.add_column("TZ")
    table.add_column("Provider")
    table.add_column("Chat")
    table.add_column("Period")
    table.add_column("Enabled")
    table.add_column("Last run")

    for task in tasks:
        period = task.params.get("stats_period", "—")
        table.add_row(
            task.display_id(),
            task.cron,
            task.timezone,
            task.provider.value,
            task.chat_id,
            period or "—",
            "✓" if task.enabled else "✗",
            task.last_run or "—",
        )

    _console.print(table)


@posthog_report_schedule_command.command(name="remove")
@click.argument("task_id")
def posthog_report_schedule_remove(task_id: str) -> None:
    """Remove a scheduled PostHog metric report task."""
    from platform.scheduler.store import get_task, remove_task
    from platform.scheduler.types import TaskKind

    task = get_task(task_id)
    if task is None or task.kind != TaskKind.POSTHOG_METRIC_REPORT:
        _console.print(f"[red]Error: PostHog report task {task_id} not found.[/red]")
        raise SystemExit(1)

    if remove_task(task_id):
        _console.print(f"[green]Task {task_id} removed.[/green]")
    else:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)


@posthog_report_schedule_command.command(name="run")
@click.argument("task_id")
def posthog_report_schedule_run(task_id: str) -> None:
    """Run a scheduled PostHog metric report task immediately."""
    from integrations.posthog.report_prerequisites import (
        require_posthog_integration,
        require_report_delivery_provider,
    )
    from platform.scheduler.runner import run_task_now
    from platform.scheduler.store import get_task
    from platform.scheduler.types import TaskKind

    install_runtime()
    task = get_task(task_id)
    if task is None or task.kind != TaskKind.POSTHOG_METRIC_REPORT:
        _console.print(f"[red]Error: PostHog report task {task_id} not found.[/red]")
        raise SystemExit(1)

    require_posthog_integration()
    require_report_delivery_provider(task.provider.value)

    _console.print(f"Running PostHog report task {task_id}...")
    success = run_task_now(task_id)
    if success:
        _console.print("[green]Done.[/green]")
    else:
        _console.print("[red]Task execution failed. Check logs for details.[/red]")
        raise SystemExit(1)


__all__ = ["posthog_command"]
