"""Slash ``/remote-sync`` — same service as ``opensre remote-sync`` and gateway ports."""

from __future__ import annotations

import logging

from rich.console import Console

from config.constants.filestorage import DEFAULT_REMOTE_SYNC_PROVIDER
from platform.filestorage import OrgScopeNotSupportedError, RemoteSyncError
from platform.filestorage.enums import RemoteSyncSubcommand
from platform.filestorage.messages import DISABLED_HELP, format_report_lines
from platform.filestorage.operations import get_sync_status, run_remote_sync
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT

logger = logging.getLogger(__name__)

_USAGE = (
    f"/remote-sync [{RemoteSyncSubcommand.STATUS}|{RemoteSyncSubcommand.SYNC}] "
    "[--pull-only|--push-only]"
)


def _print_lines(
    console: Console, lines: tuple[str, ...] | list[str], *, dim_last: bool = False
) -> None:
    for index, line in enumerate(lines):
        if dim_last and index == len(lines) - 1:
            console.print(f"[{DIM}]{line}[/]")
        else:
            console.print(line)


def _print_status(console: Console) -> bool:
    status = get_sync_status()
    if not status.enabled or status.config is None:
        console.print(f"[{DIM}]{DISABLED_HELP}[/]")
        return True
    cfg = status.config
    console.print(f"Remote sync is on ({cfg.provider}) → [{HIGHLIGHT}]{cfg.bucket}/{cfg.prefix}[/]")
    for root in status.roots:
        state = "exists" if root.exists else "not created yet"
        console.print(f"  {root.name:<10} {root.path} [{DIM}]({state})[/]")
    console.print(f"[{DIM}]Never uploaded: integration credentials and model keys.[/]")
    return True


def _run_sync(console: Console, args: list[str]) -> bool:
    flags = {a.lower() for a in args}
    with console.status("syncing…", spinner="dots"):
        report = run_remote_sync(
            pull_only="--pull-only" in flags,
            push_only="--push-only" in flags,
        )
    if report is None:
        console.print(f"[{DIM}]{DISABLED_HELP}[/]")
        return True
    lines = format_report_lines(report)
    _print_lines(console, lines, dim_last=bool(report.kept_remote))
    return True


def _parse_subcommand(raw: str) -> RemoteSyncSubcommand | None:
    try:
        return RemoteSyncSubcommand(raw)
    except ValueError:
        return None


def _cmd_remote_sync(_session: Session, console: Console, args: list[str]) -> bool:
    token = (args[0] if args else RemoteSyncSubcommand.STATUS.value).strip().lower()
    sub = RemoteSyncSubcommand.STATUS if not token else _parse_subcommand(token)
    try:
        if sub is RemoteSyncSubcommand.STATUS:
            return _print_status(console)
        if sub is RemoteSyncSubcommand.SYNC:
            return _run_sync(console, args[1:])
    except OrgScopeNotSupportedError as exc:
        # Safe to show: our own wording, no vendor or credential detail.
        console.print(f"[{DIM}]{exc}[/]")
        return True
    except RemoteSyncError:
        # This handler also serves gateway chat, an external surface, so the
        # provider's message never reaches the reply. Detail goes to the log.
        logger.warning("[remote-sync] command failed", exc_info=True)
        console.print(
            f"[{ERROR}]Sync failed.[/] Check the opensre log, "
            "or run [bold]opensre remote-sync status[/bold] locally for detail."
        )
        return True
    console.print(
        f"[{ERROR}]unknown subcommand:[/] {token}  "
        f"(try [bold]/remote-sync {RemoteSyncSubcommand.STATUS}[/bold])"
    )
    return True


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        "/remote-sync",
        "Mirror your sessions and memory to your own object store.",
        _cmd_remote_sync,
        usage=(_USAGE,),
        notes=(
            "Off unless OPENSRE_REMOTE_SYNC is set. Default provider is "
            f"{DEFAULT_REMOTE_SYNC_PROVIDER}; set OPENSRE_REMOTE_SYNC_PROVIDER "
            "to choose another registered backend. Integration credentials and "
            "model keys are never uploaded. Same service as "
            "`opensre remote-sync` (gateway clients share this slash command).",
        ),
        first_arg_completions=(
            (
                RemoteSyncSubcommand.STATUS.value,
                "show whether sync is on and what would be mirrored",
            ),
            (RemoteSyncSubcommand.SYNC.value, "pull remote changes, then push local ones"),
        ),
        use_cases=(
            "sync my conversations to S3",
            "back up my opensre memory",
            "set up opensre on my second laptop",
        ),
    ),
)

__all__ = ["COMMANDS"]
