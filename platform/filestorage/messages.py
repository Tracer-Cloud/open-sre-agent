"""Wording shown to a person after a remote-sync command.

Kept apart from :mod:`platform.filestorage.operations` so the CLI, the shell,
and any chat sink render the same sentences, and so changing a sentence never
means touching the code that moves files. Every function here is pure.
"""

from __future__ import annotations

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PROVIDER,
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from platform.filestorage.engine import SyncReport
from platform.filestorage.operations import SyncStatus


def _human_size(n: int) -> str:
    """Format byte count as a human-readable string (e.g. ``"1.2 KiB"``)."""
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KiB"
    if n < 1024**3:
        return f"{n / 1024**2:.1f} MiB"
    return f"{n / 1024**3:.1f} GiB"


DISABLED_HELP = f"""Remote sync is off.

To turn it on, point opensre at a store you own:

    export {REMOTE_SYNC_ENV}=1
    export {REMOTE_SYNC_BUCKET_ENV}=my-opensre-bucket

Optional: {REMOTE_SYNC_PROVIDER_ENV} (default {DEFAULT_REMOTE_SYNC_PROVIDER}), \
{REMOTE_SYNC_PREFIX_ENV}, {REMOTE_SYNC_REGION_ENV}, {REMOTE_SYNC_PROFILE_ENV}.
Cloud credentials are read from the usual places; opensre never stores them."""

_KEPT_REMOTE_HINT = (
    "Some files were left alone because the store held a newer copy. "
    "Run a full sync (no --push-only) to take those changes first."
)


def format_status_lines(status: SyncStatus) -> tuple[str, ...]:
    """Plain-text status lines for CLI, REPL, or gateway sinks (pure)."""
    if not status.enabled or status.config is None:
        return (DISABLED_HELP,)
    cfg = status.config
    lines: list[str] = [
        f"Remote sync is on ({cfg.provider}) → {cfg.bucket}/{cfg.prefix}",
        "Mirrored:",
    ]
    for root in status.roots:
        state = "exists" if root.exists else "not created yet"
        lines.append(f"  {root.name:<10} {root.path} ({state})")
    lines.append("Never uploaded: integration credentials and model keys.")
    return tuple(lines)


def format_report_lines(report: SyncReport) -> tuple[str, ...]:
    """Plain-text result lines after a successful run (pure; snapshots lists)."""
    downloaded = list(report.downloaded)
    uploaded = list(report.uploaded)
    kept_remote = list(report.kept_remote)
    skipped = report.skipped
    down_size = f" ({_human_size(report.downloaded_bytes)})" if report.downloaded_bytes else ""
    up_size = f" ({_human_size(report.uploaded_bytes)})" if report.uploaded_bytes else ""
    total_size = f" ({_human_size(report.total_bytes)} total)" if report.total_bytes else ""
    lines: list[str] = [
        f"Sync complete — {len(downloaded)} downloaded{down_size}, "
        f"{len(uploaded)} uploaded{up_size}, {skipped} already current{total_size}."
    ]
    if kept_remote:
        lines.append(f"{len(kept_remote)} kept the store's newer copy:")
        lines.extend(f"  {key}" for key in kept_remote)
        lines.append(_KEPT_REMOTE_HINT)
    return tuple(lines)


__all__ = [
    "DISABLED_HELP",
    "format_report_lines",
    "format_status_lines",
]
