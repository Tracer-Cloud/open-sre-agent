"""Optional mirroring of conversation context to a store the user owns.

Off by default. When switched on, conversation history and memory are copied
to the user's own object store (built-in: AWS/S3, GCS, and Vercel Blob; others
register under :mod:`platform.filestorage.providers`). Credentials never leave
the machine - see :mod:`platform.filestorage.syncable`.

Surfaces share one **stateless, thread-safe** service
(:mod:`platform.filestorage.operations`): ``opensre remote-sync``, REPL
``/remote-sync``, and gateway headless slash ports. Setup writes the stored
``remote_sync`` section; each sync re-reads settings and builds a fresh backend.
Roots follow the active principal scope (``sessions_dir`` / ``get_memory_dir``).

This is the object-store counterpart to a mounted org context root: same idea,
different mechanism, because a laptop has no provisioned filesystem and the
stores here write by atomic rename.
"""

from __future__ import annotations

from platform.filestorage.config import (
    RemoteSyncConfig,
    load_remote_sync_config,
    remote_sync_enabled,
)
from platform.filestorage.engine import (
    SyncReport,
    local_files,
    pull,
    push,
    relative_key,
    resolve_direction,
    run_sync,
)
from platform.filestorage.enums import (
    BuiltInProvider,
    RemoteSyncSubcommand,
    SyncDirection,
    SyncRootName,
)
from platform.filestorage.errors import (
    OrgScopeNotSupportedError,
    RemoteSyncConfigError,
    RemoteSyncError,
    RemoteSyncUnavailableError,
    UnsyncablePathError,
)
from platform.filestorage.exclusions import (
    NO_EXCLUSIONS,
    ExclusionRules,
    parse_exclusions,
)
from platform.filestorage.messages import (
    DISABLED_HELP,
    NO_EXCLUSIONS_HELP,
    format_exclusion_lines,
    format_report_lines,
    format_status_lines,
    root_state,
)
from platform.filestorage.operations import (
    SyncRootStatus,
    SyncStatus,
    get_sync_status,
    run_remote_sync,
)
from platform.filestorage.ports import ObjectStore, RemoteObject
from platform.filestorage.providers import build_object_store
from platform.filestorage.syncable import SyncRoot, is_syncable, resolved_roots, syncable_roots

__all__ = [
    "NO_EXCLUSIONS",
    "NO_EXCLUSIONS_HELP",
    "ExclusionRules",
    "OrgScopeNotSupportedError",
    "format_exclusion_lines",
    "format_status_lines",
    "format_report_lines",
    "DISABLED_HELP",
    "BuiltInProvider",
    "ObjectStore",
    "RemoteObject",
    "RemoteSyncConfig",
    "RemoteSyncConfigError",
    "RemoteSyncError",
    "RemoteSyncSubcommand",
    "RemoteSyncUnavailableError",
    "SyncDirection",
    "SyncReport",
    "SyncRoot",
    "SyncRootName",
    "SyncRootStatus",
    "SyncStatus",
    "UnsyncablePathError",
    "build_object_store",
    "get_sync_status",
    "is_syncable",
    "load_remote_sync_config",
    "local_files",
    "parse_exclusions",
    "pull",
    "push",
    "relative_key",
    "remote_sync_enabled",
    "resolve_direction",
    "resolved_roots",
    "root_state",
    "run_remote_sync",
    "run_sync",
    "syncable_roots",
]
