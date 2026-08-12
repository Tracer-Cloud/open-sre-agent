"""Example and characterization tests for registering custom ObjectStores.

Extension Path for Third-Party Providers:
1. Implement the :class:`~platform.filestorage.ports.ObjectStore` protocol:
   - ``list_objects(self, prefix: str) -> list[RemoteObject]``
   - ``get_object(self, key: str) -> bytes``
   - ``put_object(self, key: str, data: bytes) -> None``
   - ``describe(self) -> str``

2. Register the provider using ``register_object_store(name, factory)`` where:
   - ``name`` is a string identifier for the provider (set in the config's provider field, e.g., via ``OPENSRE_REMOTE_SYNC_PROVIDER``).
   - ``factory`` is a callable that receives a ``RemoteSyncConfig`` and returns the ``ObjectStore`` instance.

3. Clean up custom registrations in test environments by calling ``unregister_object_store(name)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.engine import pull, push
from platform.filestorage.enums import SyncRootName
from platform.filestorage.ports import ObjectStore, RemoteObject
from platform.filestorage.providers.registry import (
    build_object_store,
    register_object_store,
    unregister_object_store,
)
from platform.filestorage.syncable import SyncRoot


class CustomFakeStore:
    """A minimal fake ObjectStore implementation for testing the extension path."""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket
        self.storage: dict[str, bytes] = {}

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        from datetime import UTC, datetime
        from platform.filestorage.engine import content_tag

        return [
            RemoteObject(
                key=k,
                size=len(v),
                last_modified=datetime.now(tz=UTC),
                etag=content_tag(v),
            )
            for k, v in self.storage.items()
            if k.startswith(prefix)
        ]

    def get_object(self, key: str) -> bytes:
        return self.storage[key]

    def put_object(self, key: str, data: bytes) -> None:
        self.storage[key] = data

    def describe(self) -> str:
        return f"custom-fake://{self.bucket}"


@pytest.fixture
def custom_provider_name() -> str:
    return "custom-fake-provider"


@pytest.fixture
def registered_store(custom_provider_name: str) -> Iterator[CustomFakeStore]:
    """Fixture that registers a custom provider, yields the store instance, and unregisters it in teardown."""
    # Arrange
    store_instance = CustomFakeStore(bucket="my-temp-bucket")
    register_object_store(custom_provider_name, lambda _cfg: store_instance)

    yield store_instance

    # Teardown / Cleanup
    unregister_object_store(custom_provider_name)


def test_custom_provider_can_be_registered_and_built(
    custom_provider_name: str, registered_store: CustomFakeStore
) -> None:
    # Arrange
    config = RemoteSyncConfig(
        provider=custom_provider_name,
        bucket="my-temp-bucket",
    )

    # Act
    built_store = build_object_store(config)

    # Assert
    assert isinstance(built_store, CustomFakeStore)
    assert built_store is registered_store
    assert built_store.describe() == "custom-fake://my-temp-bucket"


def test_custom_provider_can_push_and_pull_files(
    custom_provider_name: str, registered_store: CustomFakeStore, tmp_path: Path
) -> None:
    # Arrange
    # Create local source files (using newline="\n" to be safe on Windows)
    local_source_dir = tmp_path / "source"
    local_source_dir.mkdir()
    session_dir = local_source_dir / "sessions"
    session_dir.mkdir()
    (session_dir / "turn-1.jsonl").write_text('{"turn": 1}\n', encoding="utf-8", newline="\n")

    source_roots = (SyncRoot(name=SyncRootName.SESSIONS, path=session_dir),)

    # Create destination directory for pull
    local_dest_dir = tmp_path / "dest"
    local_dest_dir.mkdir()
    dest_session_dir = local_dest_dir / "sessions"
    dest_session_dir.mkdir()

    dest_roots = (SyncRoot(name=SyncRootName.SESSIONS, path=dest_session_dir),)

    # Act
    # 1. Push from source to the registered store
    push_report = push(registered_store, roots=source_roots)

    # 2. Pull from registered store to destination
    pull_report = pull(registered_store, roots=dest_roots)

    # Assert
    # Verify push
    assert "sessions/turn-1.jsonl" in push_report.uploaded
    assert "sessions/turn-1.jsonl" in registered_store.storage

    # Verify pull
    assert "sessions/turn-1.jsonl" in pull_report.downloaded
    dest_file = dest_session_dir / "turn-1.jsonl"
    assert dest_file.exists()
    assert dest_file.read_text(encoding="utf-8") == '{"turn": 1}\n'
