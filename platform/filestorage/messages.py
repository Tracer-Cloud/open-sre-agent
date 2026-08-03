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
    REMOTE_SYNC_EXCLUDE_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.engine import SyncReport
from platform.filestorage.exclusions import ExclusionRules
from platform.filestorage.operations import SyncRootStatus, SyncStatus
from platform.filestorage.providers import credential_hint_for_provider
from platform.filestorage.providers.registry import builtin_providers


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

To turn it on:

    opensre remote-sync setup
    # or: /remote-sync setup

Or set env vars:

    export {REMOTE_SYNC_ENV}=1
    export {REMOTE_SYNC_BUCKET_ENV}=my-opensre-bucket

Optional: {REMOTE_SYNC_PROVIDER_ENV} (default {DEFAULT_REMOTE_SYNC_PROVIDER}; \
built-in: {", ".join(builtin_providers())}), {REMOTE_SYNC_PREFIX_ENV}, {REMOTE_SYNC_REGION_ENV}, \
{REMOTE_SYNC_PROFILE_ENV}. Cloud credentials stay ambient; opensre never stores them."""

_KEPT_REMOTE_HINT = (
    "Some files were left alone because the store held a newer copy. "
    "Run a full sync (no --push-only) to take those changes first."
)

NO_EXCLUSIONS_HELP = (
    f"Nothing is excluded. Set {REMOTE_SYNC_EXCLUDE_ENV} to a comma-separated list of "
    "glob patterns, or a list under remote_sync.exclude, to keep paths off the store."
)


def root_state(root: SyncRootStatus) -> str:
    """The parenthetical after a root's path, e.g. ``exists, 3 excluded``.

    Shared so the plain-text and the styled surfaces cannot drift into
    describing the same root two different ways.
    """
    state = "exists" if root.exists else "not created yet"
    if root.excluded:
        return f"{state}, {root.excluded} excluded"
    return state


def format_exclusion_lines(exclusions: ExclusionRules) -> tuple[str, ...]:
    """The patterns in force, or one line explaining how to set some (pure)."""
    if not exclusions:
        return (NO_EXCLUSIONS_HELP,)
    return ("Excluded by your settings:", *(f"  {pattern}" for pattern in exclusions.patterns))


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
        lines.append(f"  {root.name:<10} {root.path} ({root_state(root)})")
    lines.extend(format_exclusion_lines(status.exclusions))
    lines.append("Never uploaded: integration credentials and model keys.")
    return tuple(lines)


def format_report_lines(report: SyncReport, *, dry_run: bool = False) -> tuple[str, ...]:
    """Plain-text result lines after a run (pure; snapshots lists).

    ``dry_run`` only changes the wording — the report already reflects a
    preview when the caller ran the sync that way.
    """
    downloaded = list(report.downloaded)
    uploaded = list(report.uploaded)
    kept_remote = list(report.kept_remote)
    skipped = report.skipped
    down_size = f" ({_human_size(report.downloaded_bytes)})" if report.downloaded_bytes else ""
    up_size = f" ({_human_size(report.uploaded_bytes)})" if report.uploaded_bytes else ""
    total_size = f" ({_human_size(report.total_bytes)} total)" if report.total_bytes else ""
    excluded = len(report.excluded)
    heading = "Dry run" if dry_run else "Sync complete"
    would = " would be" if dry_run else ""
    lines: list[str] = [
        f"{heading} — {len(downloaded)}{would} downloaded{down_size}, "
        f"{len(uploaded)}{would} uploaded{up_size}, {skipped} already current{total_size}."
    ]
    if excluded:
        # Its own line rather than a fourth clause in the summary: a run with no
        # exclusions configured must read exactly as it did before the feature.
        lines.append(f"{excluded} held back by your exclude settings.")
    if kept_remote:
        lines.append(f"{len(kept_remote)} kept the store's newer copy:")
        lines.extend(f"  {key}" for key in kept_remote)
        lines.append(_KEPT_REMOTE_HINT)
    return tuple(lines)


def format_setup_lines(config: RemoteSyncConfig, *, enabled: bool = True) -> tuple[str, ...]:
    """Confirm settings were written and how to supply ambient credentials."""
    lines = [
        f"Remote sync settings saved → {config.provider} / {config.bucket}/{config.prefix}",
        "Stored in ~/.opensre/config.yml (env vars still win for a single run).",
        credential_hint_for_provider(config.provider),
    ]
    if enabled:
        lines.append("Next: opensre remote-sync status   then   opensre remote-sync sync")
    else:
        lines.append("Remote sync is off; the settings stay for when you turn it back on.")
    return tuple(lines)


SETUP_DISABLED_CONFIRM = (
    "Remote sync is off. Saved enabled: false to ~/.opensre/config.yml "
    "(stored provider/bucket kept for when you turn it back on)."
)


__all__ = [
    "DISABLED_HELP",
    "NO_EXCLUSIONS_HELP",
    "SETUP_DISABLED_CONFIRM",
    "format_exclusion_lines",
    "format_report_lines",
    "format_setup_lines",
    "format_status_lines",
    "root_state",
]
