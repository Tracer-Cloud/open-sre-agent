"""Optional mirroring of conversation context to a store the user owns.

Off by default. When switched on, conversation history and memory are copied
to the user's own object store (built-in: AWS/S3, GCS, and Vercel Blob; others
register under :mod:`infrastructure.filestorage.providers`). Credentials never leave
the machine - see :mod:`infrastructure.filestorage.syncable`.

Surfaces share one **stateless, thread-safe** service
(:mod:`infrastructure.filestorage.operations`): ``opensre remote-sync``, REPL
``/remote-sync``, and gateway headless slash ports. Setup writes the stored
``remote_sync`` section; each sync re-reads settings and builds a fresh backend.
Roots follow the active principal scope (``sessions_dir`` / ``get_memory_dir``).

This is the object-store counterpart to a mounted org context root: same idea,
different mechanism, because a laptop has no provisioned filesystem and the
stores here write by atomic rename.
"""

from __future__ import annotations

from infrastructure.filestorage.config import (
    RemoteSyncConfig,
    load_remote_sync_config,
    remote_sync_enabled,
)
from infrastructure.filestorage.engine import (
    SyncReport,
    local_files,
    pull,
    push,
    relative_key,
    resolve_direction,
    run_sync,
)
from infrastructure.filestorage.enums import (
    BucketExposure,
    BuiltInProvider,
    RemoteSyncSubcommand,
    SyncDirection,
    SyncRootName,
)
from infrastructure.filestorage.errors import (
    OrgScopeNotSupportedError,
    RemoteSyncConfigError,
    RemoteSyncError,
    RemoteSyncUnavailableError,
    UnsyncablePathError,
)
from infrastructure.filestorage.exclusions import (
    NO_EXCLUSIONS,
    ExclusionRules,
    parse_exclusions,
)
from infrastructure.filestorage.exposure import PublicAccessStatus
from infrastructure.filestorage.messages import (
    DISABLED_HELP,
    NO_EXCLUSIONS_HELP,
    format_exclusion_lines,
    format_exposure_line,
    format_report_lines,
    format_status_lines,
    root_state,
)
from infrastructure.filestorage.operations import (
    SyncRootStatus,
    SyncStatus,
    get_sync_status,
    run_remote_sync,
)
from infrastructure.filestorage.ports import ObjectStore, RemoteObject
from infrastructure.filestorage.providers import build_object_store, check_bucket_exposure
from infrastructure.filestorage.syncable import (
    SyncRoot,
    is_syncable,
    resolved_roots,
    syncable_roots,
)

__all__ = [
    "NO_EXCLUSIONS",
    "NO_EXCLUSIONS_HELP",
    "BucketExposure",
    "ExclusionRules",
    "OrgScopeNotSupportedError",
    "PublicAccessStatus",
    "format_exclusion_lines",
    "format_exposure_line",
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
    "check_bucket_exposure",
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
