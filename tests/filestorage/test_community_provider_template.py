"""Extension-path template: how a community ObjectStore backend plugs in.

This file is not testing platform code that changes often — it exists as a
worked example for anyone adding a new cloud backend (an S3-alternative, a
self-hosted blob store, etc.) to remote sync. The whole contract is:

1. Implement the four :class:`~platform.filestorage.ports.ObjectStore`
   protocol methods: ``list_objects``, ``get_object``, ``put_object``,
   ``describe``.
2. Call :func:`~platform.filestorage.providers.registry.register_object_store`
   with a provider name and a factory — typically at import time in your own
   provider module, the way ``platform/filestorage/providers/aws.py`` does.
3. Nothing else changes: ``RemoteSyncConfig(provider="your-name", ...)`` plus
   :func:`~platform.filestorage.providers.registry.build_object_store` resolve
   to your factory automatically — the engine, CLI, and REPL never import a
   vendor module directly.
4. :func:`~platform.filestorage.engine.push` and
   :func:`~platform.filestorage.engine.pull` talk only to the ``ObjectStore``
   protocol, so a new backend gets the full sync engine for free, exercised
   below with real temp directories rather than mocks.
5. Tests unregister what they registered (see the ``fake_store`` fixture) so
   a test-only provider never leaks into another test file that calls
   :func:`~platform.filestorage.providers.registry.registered_providers` or
   ``build_object_store``.

See ``platform/filestorage/providers/aws.py`` for the same five steps against
a real SDK.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.engine import content_tag, pull, push
from platform.filestorage.enums import SyncRootName
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers.registry import (
    build_object_store,
    register_object_store,
    unregister_object_store,
)
from platform.filestorage.syncable import SyncRoot

_PROVIDER_NAME = "fake"


class _FakeObjectStore:
    """The minimum a community backend implements: the four ObjectStore methods."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [
            RemoteObject(
                key=key,
                size=len(data),
                last_modified=datetime.now(tz=UTC),
                etag=content_tag(data),
            )
            for key, data in self._objects.items()
            if key.startswith(prefix)
        ]

    def get_object(self, key: str) -> bytes:
        return self._objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        self._objects[key] = data

    def describe(self) -> str:
        return f"{_PROVIDER_NAME}://template"


@pytest.fixture
def fake_store() -> Iterator[_FakeObjectStore]:
    """Registers "fake" for one test and unregisters it afterward.

    Mirrors how a real provider module registers at import time
    (``platform/filestorage/providers/aws.py``), scoped to a single test so no
    fake store leaks into ``build_object_store`` for any other test file.
    """
    store = _FakeObjectStore()
    register_object_store(_PROVIDER_NAME, lambda _config: store)
    yield store
    unregister_object_store(_PROVIDER_NAME)


def test_a_registered_fake_provider_is_built_via_the_registry(
    fake_store: _FakeObjectStore,
) -> None:
    # Arrange
    config = RemoteSyncConfig(bucket="template-bucket", provider=_PROVIDER_NAME)

    # Act
    built = build_object_store(config)

    # Assert
    assert built is fake_store
    assert built.describe() == "fake://template"


def test_push_then_pull_round_trips_a_file_through_the_fake_store(
    fake_store: _FakeObjectStore, tmp_path: Path
) -> None:
    """Full push/pull round trip against the engine, the way a real sync runs."""
    # Arrange
    push_root = tmp_path / "push_side" / "sessions"
    pull_root = tmp_path / "pull_side" / "sessions"
    push_root.mkdir(parents=True)
    pull_root.mkdir(parents=True)
    (push_root / "example.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    push_roots = (SyncRoot(name=SyncRootName.SESSIONS, path=push_root),)
    pull_roots = (SyncRoot(name=SyncRootName.SESSIONS, path=pull_root),)

    # Act
    push_report = push(fake_store, roots=push_roots)
    pull_report = pull(fake_store, roots=pull_roots)

    # Assert
    assert "sessions/example.jsonl" in push_report.uploaded
    assert "sessions/example.jsonl" in pull_report.downloaded
    assert (pull_root / "example.jsonl").read_text(encoding="utf-8") == '{"turn": 1}\n'
