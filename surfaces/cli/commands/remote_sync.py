"""``opensre remote-sync`` — thin Click adapter over :mod:`platform.filestorage`."""

from __future__ import annotations

import sys

import click

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
)
from platform.common.exit_codes import ERROR, SUCCESS
from platform.filestorage import RemoteSyncConfigError, RemoteSyncError
from platform.filestorage.enums import RemoteSyncSubcommand
from platform.filestorage.messages import (
    DISABLED_HELP,
    format_report_lines,
    format_setup_lines,
    format_status_lines,
)
from platform.filestorage.operations import get_sync_status, run_remote_sync
from platform.filestorage.providers.registry import credential_hint_for_provider
from platform.filestorage.setup import RemoteSyncSetupRequest, save_remote_sync_settings


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


@remote_sync_command.command(name=RemoteSyncSubcommand.SETUP.value)
@click.option(
    "--provider",
    default=None,
    help=f"Backend name (default {DEFAULT_REMOTE_SYNC_PROVIDER}; built-in: aws, gcs).",
)
@click.option("--bucket", default=None, help="Store name you own (S3 or GCS bucket).")
@click.option("--prefix", default=None, help=f"Key prefix (default {DEFAULT_REMOTE_SYNC_PREFIX}).")
@click.option("--region", default=None, help="Region override when the provider supports it.")
@click.option("--profile", default=None, help="Named credentials profile (AWS).")
@click.option(
    "--enabled/--disabled",
    default=True,
    show_default=True,
    help="Whether remote sync is switched on in stored settings.",
)
def setup_command(
    provider: str | None,
    bucket: str | None,
    prefix: str | None,
    region: str | None,
    profile: str | None,
    enabled: bool,
) -> None:
    """Write remote-sync settings to ~/.opensre/config.yml (prompts if flags omitted)."""
    try:
        request = _collect_setup_request(
            provider=provider,
            bucket=bucket,
            prefix=prefix,
            region=region,
            profile=profile,
            enabled=enabled,
        )
        config = save_remote_sync_settings(request)
    except (RemoteSyncError, click.Abort) as exc:
        if isinstance(exc, click.Abort):
            raise SystemExit(ERROR) from exc
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc
    for line in format_setup_lines(
        config, credential_hint_for_provider(config.provider), enabled=request.enabled
    ):
        click.echo(line)
    raise SystemExit(SUCCESS)


def _collect_setup_request(
    *,
    provider: str | None,
    bucket: str | None,
    prefix: str | None,
    region: str | None,
    profile: str | None,
    enabled: bool,
) -> RemoteSyncSetupRequest:
    """Flags when present; prompt on a TTY, never silently in a pipe."""
    if provider is None and sys.stdin.isatty():
        provider = click.prompt("Provider", default=DEFAULT_REMOTE_SYNC_PROVIDER)
    if (bucket is None or not bucket.strip()) and sys.stdin.isatty():
        bucket = click.prompt("Bucket (store name you own)")
    if prefix is None and sys.stdin.isatty():
        prefix = click.prompt("Key prefix", default=DEFAULT_REMOTE_SYNC_PREFIX)
    if bucket is None or not bucket.strip():
        raise RemoteSyncConfigError(
            "setup needs a bucket: pass --bucket (and optionally --provider/--prefix), "
            "or run it on a terminal"
        )
    return RemoteSyncSetupRequest(
        bucket=bucket,
        provider=provider or DEFAULT_REMOTE_SYNC_PROVIDER,
        prefix=prefix or DEFAULT_REMOTE_SYNC_PREFIX,
        region=region or "",
        profile=profile or "",
        enabled=enabled,
    )


__all__ = ["remote_sync_command"]
