"""Closed remote-sync vocabularies stay aligned with env defaults and surfaces."""

from __future__ import annotations

from config.constants.filestorage import DEFAULT_REMOTE_SYNC_PROVIDER
from platform.filestorage.engine import resolve_direction
from platform.filestorage.enums import (
    BuiltInProvider,
    RemoteSyncSubcommand,
    SyncDirection,
    SyncRootName,
)
from platform.filestorage.syncable import syncable_roots


def test_builtin_provider_matches_env_default() -> None:
    assert DEFAULT_REMOTE_SYNC_PROVIDER == BuiltInProvider.AWS
    assert BuiltInProvider.AWS.value == "aws"
    assert BuiltInProvider.GCS.value == "gcs"
    assert BuiltInProvider.VERCEL.value == "vercel"


def test_syncable_roots_use_root_name_enum() -> None:
    names = {root.name for root in syncable_roots()}
    assert names == {SyncRootName.SESSIONS, SyncRootName.MEMORY}


def test_direction_flags_resolve_to_enum() -> None:
    assert resolve_direction(pull_only=True, push_only=False) is SyncDirection.PULL
    assert resolve_direction(pull_only=False, push_only=True) is SyncDirection.PUSH
    assert resolve_direction(pull_only=False, push_only=False) is SyncDirection.BOTH


def test_remote_sync_subcommands_are_status_sync_setup() -> None:
    assert set(RemoteSyncSubcommand) == {
        RemoteSyncSubcommand.STATUS,
        RemoteSyncSubcommand.SYNC,
        RemoteSyncSubcommand.SETUP,
    }
    assert RemoteSyncSubcommand("status") is RemoteSyncSubcommand.STATUS
    assert RemoteSyncSubcommand("setup") is RemoteSyncSubcommand.SETUP
