"""``opensre sync`` — mirror conversation history and memory to your own bucket."""

from __future__ import annotations

import click

from config.constants.filestorage import (
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from platform.common.exit_codes import ERROR, SUCCESS
from platform.filestorage import (
    RemoteSyncConfig,
    RemoteSyncError,
    SyncDirection,
    SyncReport,
    load_remote_sync_config,
    resolve_direction,
    run_sync,
    syncable_roots,
)

_DISABLED_HELP = f"""Remote sync is off.

To turn it on, point opensre at a bucket you own:

    export {REMOTE_SYNC_ENV}=1
    export {REMOTE_SYNC_BUCKET_ENV}=my-opensre-bucket

Optional: {REMOTE_SYNC_PREFIX_ENV}, {REMOTE_SYNC_REGION_ENV}, {REMOTE_SYNC_PROFILE_ENV}.
Your AWS credentials are read from the usual places; opensre never stores them."""


@click.group(name="sync", invoke_without_command=True)
@click.pass_context
def sync_command(ctx: click.Context) -> None:
    """Mirror sessions and memory to your own S3 bucket."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(status_command)


@sync_command.command(name="status")
def status_command() -> None:
    """Show whether sync is on, and what would be mirrored."""
    try:
        config = load_remote_sync_config()
    except RemoteSyncError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc
    if config is None:
        click.echo(_DISABLED_HELP)
        raise SystemExit(SUCCESS)

    click.echo(f"Remote sync is on → s3://{config.bucket}/{config.prefix}")
    click.echo("Mirrored:")
    for root in syncable_roots():
        state = "exists" if root.path.is_dir() else "not created yet"
        click.echo(f"  {root.name:<10} {root.path} ({state})")
    click.echo("Never uploaded: integration credentials and model keys.")


@sync_command.command(name="run")
@click.option("--pull-only", is_flag=True, help="Download only; send nothing.")
@click.option("--push-only", is_flag=True, help="Upload only; fetch nothing.")
def run_command(pull_only: bool, push_only: bool) -> None:
    """Sync now: pull remote changes, then push local ones."""
    try:
        direction = resolve_direction(pull_only=pull_only, push_only=push_only)
        config = load_remote_sync_config()
        if config is None:
            click.echo(_DISABLED_HELP)
            raise SystemExit(SUCCESS)
        report = _run_sync(config, direction=direction)
    except RemoteSyncError as exc:
        click.echo(f"Sync failed: {exc}", err=True)
        raise SystemExit(ERROR) from exc

    click.echo(
        f"Sync complete — {len(report.downloaded)} downloaded, "
        f"{len(report.uploaded)} uploaded, {report.skipped} already current."
    )
    raise SystemExit(SUCCESS)


def _run_sync(config: RemoteSyncConfig, *, direction: SyncDirection) -> SyncReport:
    """Build the store and move the files. boto3 is imported here, not at startup."""
    from platform.filestorage.s3_store import S3ObjectStore

    return run_sync(S3ObjectStore(config), direction=direction)


__all__ = ["sync_command"]
