"""Remote-sync actions a surface invokes.

CLI (``opensre remote-sync``), interactive shell (``/remote-sync``), and
gateway clients all call here, so none of them re-implements config loading,
store building, or engine ordering. Surfaces own only their own I/O.

**Stateless:** no cached config, ObjectStore, or report. Every call re-reads
env/settings, resolves roots for the current scope, and builds a fresh store.
**Thread-safe:** safe to call concurrently from many turns; results are newly
allocated and not shared. Two syncs of the same roots can still race at the
filesystem / object-store layer (last writer wins) — that is external I/O.

Roots come from ``sessions_dir()`` / ``get_memory_dir()``, so the active
principal scope (laptop home vs org user tree) is already applied.

User-facing wording lives in :mod:`platform.filestorage.messages`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.scope_context import current_scope
from platform.filestorage.config import RemoteSyncConfig, load_remote_sync_config
from platform.filestorage.engine import SyncReport, resolve_direction, run_sync
from platform.filestorage.enums import SyncDirection, SyncRootName
from platform.filestorage.errors import OrgScopeNotSupportedError
from platform.filestorage.providers import build_object_store
from platform.filestorage.syncable import syncable_roots


@dataclass(frozen=True)
class SyncRootStatus:
    """One mirrored root as shown to the operator."""

    name: SyncRootName | str
    path: Path
    exists: bool


@dataclass(frozen=True)
class SyncStatus:
    """Whether sync is on and what would move — shared across all surfaces."""

    config: RemoteSyncConfig | None
    roots: tuple[SyncRootStatus, ...]

    @property
    def enabled(self) -> bool:
        return self.config is not None


def _owned_report(report: SyncReport) -> SyncReport:
    """Caller-owned copy so concurrent formatters cannot see later mutation."""
    return SyncReport(
        uploaded=list(report.uploaded),
        downloaded=list(report.downloaded),
        kept_remote=list(report.kept_remote),
        skipped=report.skipped,
        uploaded_bytes=report.uploaded_bytes,
        downloaded_bytes=report.downloaded_bytes,
    )


def _refuse_org_scoped_turn() -> None:
    """Fail closed when the caller is an organization member, not a laptop user.

    Object keys carry no principal or actor, so every member of an organization
    would write and read the same keys.
    """
    scope = current_scope()
    if scope is not None and scope.principal.kind == "org":
        raise OrgScopeNotSupportedError(
            "Remote sync mirrors a personal machine. This conversation belongs to "
            "an organization, whose history already persists in the shared context "
            "root, so nothing is mirrored."
        )


def get_sync_status() -> SyncStatus:
    """Load config and resolve scoped roots (no network, no cached state)."""
    _refuse_org_scoped_turn()
    config = load_remote_sync_config()
    roots = tuple(
        SyncRootStatus(name=root.name, path=root.path, exists=root.path.is_dir())
        for root in syncable_roots()
    )
    return SyncStatus(config=config, roots=roots)


def run_remote_sync(
    *,
    pull_only: bool = False,
    push_only: bool = False,
    direction: SyncDirection | None = None,
) -> SyncReport | None:
    """Pull/push for the current scope. ``None`` when sync is disabled.

    Builds a new ObjectStore per call. Returns a caller-owned report snapshot.
    Prefer ``direction=`` when the caller already has a :class:`SyncDirection`;
    the boolean flags remain for CLI/slash adapters.
    """
    _refuse_org_scoped_turn()
    resolved = (
        direction
        if direction is not None
        else resolve_direction(pull_only=pull_only, push_only=push_only)
    )
    config = load_remote_sync_config()
    if config is None:
        return None
    roots = syncable_roots()
    store = build_object_store(config)
    return _owned_report(run_sync(store, direction=resolved, roots=roots))


__all__ = [
    "SyncRootStatus",
    "SyncStatus",
    "get_sync_status",
    "run_remote_sync",
]
