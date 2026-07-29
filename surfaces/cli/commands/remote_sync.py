"""``opensre remote-sync`` — thin Click adapter over :mod:`platform.filestorage.operations`."""

from __future__ import annotations

import click

from platform.common.exit_codes import ERROR, SUCCESS
from platform.filestorage import RemoteSyncError
from platform.filestorage.enums import RemoteSyncSubcommand
from platform.filestorage.messages import (
    DISABLED_HELP,
    format_report_lines,
    format_status_lines,
)
from platform.filestorage.operations import get_sync_status, run_remote_sync


@click.group(name="remote-sync", invoke_without_command=True)
@click.pass_context
def remote_sync_command(ctx: click.Context) -> None:
    """Mirror sessions and memory to your own object store."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(status_command)


@remote_sync_command.command(name=RemoteSyncSubcommand.STATUS.value)
def status_command() -> None:
    """Show whether sync is on, and what would be mirrored."""
    try:
        lines = format_status_lines(get_sync_status())
    except RemoteSyncError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc
    for line in lines:
        click.echo(line)
    raise SystemExit(SUCCESS)


@remote_sync_command.command(name=RemoteSyncSubcommand.SYNC.value)
@click.option("--pull-only", is_flag=True, help="Download only; send nothing.")
@click.option("--push-only", is_flag=True, help="Upload only; fetch nothing.")
def sync_now_command(pull_only: bool, push_only: bool) -> None:
    """Sync now: pull remote changes, then push local ones."""
    try:
        report = run_remote_sync(pull_only=pull_only, push_only=push_only)
    except RemoteSyncError as exc:
        click.echo(f"Sync failed: {exc}", err=True)
        raise SystemExit(ERROR) from exc
    if report is None:
        click.echo(DISABLED_HELP)
        raise SystemExit(SUCCESS)
    for line in format_report_lines(report):
        click.echo(line)
    raise SystemExit(SUCCESS)


__all__ = ["remote_sync_command"]
