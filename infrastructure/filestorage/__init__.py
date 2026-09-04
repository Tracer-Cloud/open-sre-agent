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

Leaf modules are the source of truth. ``from infrastructure.filestorage import
NAME`` still works; importing this package does not load the sync engine.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, str] = {
    "NO_EXCLUSIONS": "exclusions",
    "NO_EXCLUSIONS_HELP": "messages",
    "BucketExposure": "enums",
    "ExclusionRules": "exclusions",
    "OrgScopeNotSupportedError": "errors",
    "PublicAccessStatus": "exposure",
    "format_exclusion_lines": "messages",
    "format_exposure_line": "messages",
    "format_status_lines": "messages",
    "format_report_lines": "messages",
    "DISABLED_HELP": "messages",
    "BuiltInProvider": "enums",
    "ObjectStore": "contracts",
    "RemoteObject": "contracts",
    "RemoteSyncConfig": "config",
    "RemoteSyncConfigError": "errors",
    "RemoteSyncError": "errors",
    "RemoteSyncSubcommand": "enums",
    "RemoteSyncUnavailableError": "errors",
    "SyncDirection": "enums",
    "SyncReport": "engine",
    "SyncRoot": "syncable",
    "SyncRootName": "enums",
    "SyncRootStatus": "operations",
    "SyncStatus": "operations",
    "UnsyncablePathError": "errors",
    "build_object_store": "providers",
    "check_bucket_exposure": "providers",
    "get_sync_status": "operations",
    "is_syncable": "syncable",
    "load_remote_sync_config": "config",
    "local_files": "engine",
    "parse_exclusions": "exclusions",
    "pull": "engine",
    "push": "engine",
    "relative_key": "engine",
    "remote_sync_enabled": "config",
    "resolve_direction": "engine",
    "resolved_roots": "syncable",
    "root_state": "messages",
    "run_remote_sync": "operations",
    "run_sync": "engine",
    "syncable_roots": "syncable",
}

__all__ = tuple(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load one leaf for a re-exported name, or a submodule by name."""
    leaf = _EXPORTS.get(name)
    if leaf is not None:
        value = getattr(importlib.import_module(f"{__name__}.{leaf}"), name)
        globals()[name] = value
        return value
    try:
        value = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_EXPORTS.values()))
