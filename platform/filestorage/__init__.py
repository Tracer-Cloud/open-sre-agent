"""Optional mirroring of a laptop's context to a bucket the user owns.

Off by default. When switched on, conversation history and memory are copied
to the user's own S3 bucket so a second machine can carry on the same
conversations. Credentials never leave the laptop — see
:mod:`platform.filestorage.scope`.

This is the laptop counterpart to the mounted context root a deployed
organization uses: same idea, different mechanism, because a laptop has no
provisioned filesystem and the stores here write by atomic rename.
"""

from __future__ import annotations

from platform.filestorage.config import (
    RemoteSyncConfig,
    load_remote_sync_config,
    remote_sync_enabled,
)
from platform.filestorage.errors import (
    RemoteSyncConfigError,
    RemoteSyncError,
    RemoteSyncUnavailableError,
    UnsyncablePathError,
)
from platform.filestorage.ports import ObjectStore, RemoteObject
from platform.filestorage.scope import SyncRoot, is_syncable, resolved_roots, syncable_roots
from platform.filestorage.sync import (
    SyncDirection,
    SyncReport,
    pull,
    push,
    resolve_direction,
    run_sync,
)

__all__ = [
    "ObjectStore",
    "RemoteObject",
    "RemoteSyncConfig",
    "RemoteSyncConfigError",
    "RemoteSyncError",
    "RemoteSyncUnavailableError",
    "SyncDirection",
    "SyncReport",
    "SyncRoot",
    "UnsyncablePathError",
    "is_syncable",
    "load_remote_sync_config",
    "pull",
    "push",
    "remote_sync_enabled",
    "resolve_direction",
    "resolved_roots",
    "run_sync",
    "syncable_roots",
]
