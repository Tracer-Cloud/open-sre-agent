"""Slash command: mirror sessions and memory to a user-owned S3 bucket."""

from __future__ import annotations

from rich.console import Console

from platform.filestorage import (
    RemoteSyncError,
    load_remote_sync_config,
    syncable_roots,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT

_USAGE = "/sync [status|run] [--pull-only|--push-only]"
_OFF_MESSAGE = (
    "Remote sync is off. Set OPENSRE_REMOTE_SYNC=1 and OPENSRE_REMOTE_SYNC_BUCKET "
    "to mirror sessions and memory to a bucket you own."
)


def _print_status(console: Console) -> bool:
    config = load_remote_sync_config()
    if config is None:
        console.print(f"[{DIM}]{_OFF_MESSAGE}[/]")
        return True
    console.print(f"Remote sync is on → [{HIGHLIGHT}]s3://{config.bucket}/{config.prefix}[/]")
    for root in syncable_roots():
        state = "exists" if root.path.is_dir() else "not created yet"
        console.print(f"  {root.name:<10} {root.path} [{DIM}]({state})[/]")
    console.print(f"[{DIM}]Never uploaded: integration credentials and model keys.[/]")
    return True


def _run_sync(console: Console, args: list[str]) -> bool:
    config = load_remote_sync_config()
    if config is None:
        console.print(f"[{DIM}]{_OFF_MESSAGE}[/]")
        return True

    from platform.filestorage.s3_store import S3ObjectStore
    from platform.filestorage.sync import pull, push, sync

    store = S3ObjectStore(config)
    flags = {a.lower() for a in args}
    with console.status("syncing…", spinner="dots"):
        if "--pull-only" in flags:
            report = pull(store)
        elif "--push-only" in flags:
            report = push(store)
        else:
            report = sync(store)
    console.print(
        f"Sync complete — {len(report.downloaded)} downloaded, "
        f"{len(report.uploaded)} uploaded, {report.skipped} already current."
    )
    return True


def _cmd_sync(_session: Session, console: Console, args: list[str]) -> bool:
    sub = (args[0].lower() if args else "status").strip()
    try:
        if sub in {"status", ""}:
            return _print_status(console)
        if sub == "run":
            return _run_sync(console, args[1:])
    except RemoteSyncError as exc:
        console.print(f"[{ERROR}]Sync failed:[/] {exc}")
        return True
    console.print(f"[{ERROR}]unknown subcommand:[/] {sub}  (try [bold]/sync status[/bold])")
    return True


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        "/sync",
        "Mirror your sessions and memory to your own S3 bucket.",
        _cmd_sync,
        usage=(_USAGE,),
        notes=(
            "Off unless OPENSRE_REMOTE_SYNC is set. Integration credentials and "
            "model keys are never uploaded.",
        ),
        first_arg_completions=(
            ("status", "show whether sync is on and what would be mirrored"),
            ("run", "pull remote changes, then push local ones"),
        ),
        use_cases=(
            "sync my conversations to S3",
            "back up my opensre memory",
            "set up opensre on my second laptop",
        ),
    ),
)

__all__ = ["COMMANDS"]
