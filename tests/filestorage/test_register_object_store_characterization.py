"""The community-provider extension path: register → build → push/pull.

This file is the template for adding an out-of-tree object-store backend.
``platform.filestorage`` is closed for extension except through the registry:
a backend is a tiny class implementing the four-method
:class:`~platform.filestorage.ports.ObjectStore` protocol (``list_objects``,
``get_object``, ``put_object``, ``describe``), a factory that builds it from a
:class:`~platform.filestorage.config.RemoteSyncConfig`, and one
:func:`~platform.filestorage.providers.register_object_store` call — the
engine, CLI, and REPL never change.

The tests below characterize that path end to end with an in-memory fake, so
community providers have a copy-paste template that needs no cloud account:
register the fake, build it back through the registry, and run a real
``push``/``pull`` against the engine with temp dirs. If registration breaks,
the first two tests fail before any cloud SDK is involved.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.engine import SyncReport, content_tag, pull, push
from platform.filestorage.enums import SyncRootName
from platform.filestorage.errors import RemoteSyncConfigError
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers import build_object_store
from platform.filestorage.providers.registry import (
    register_object_store,
    registered_providers,
    unregister_object_store,
)
from platform.filestorage.syncable import SyncRoot

# The provider name a community backend would register under. Must be unique in
# the process-global registry, so it is namespaced like the built-ins.
FAKE_PROVIDER = "fake"


class FakeObjectStore:
    """In-memory ObjectStore, so the engine is testable without a cloud SDK."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [
            RemoteObject(
                key=key,
                size=len(data),
                last_modified=datetime.now(tz=UTC),
                etag=content_tag(data),
            )
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def describe(self) -> str:
        return "fake://bucket"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A laptop ~/.opensre with sessions and memory to mirror."""
    (tmp_path / "sessions").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / "sessions" / "abc.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    (tmp_path / "memory" / "a-fact.md").write_text("remembered\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def roots(home: Path) -> tuple[SyncRoot, ...]:
    return (
        SyncRoot(name=SyncRootName.SESSIONS, path=home / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=home / "memory"),
    )


@pytest.fixture(autouse=True)
def _restore_registry() -> Iterator[None]:
    """Registrations are process-global, so snapshot and restore them."""
    from platform.filestorage.providers import registry as reg

    with reg._REGISTRY_LOCK:
        snap = dict(reg._REGISTRY)
        caps = dict(reg._MAX_PARALLEL_UPLOADS)
    yield
    with reg._REGISTRY_LOCK:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(snap)
        reg._MAX_PARALLEL_UPLOADS.clear()
        reg._MAX_PARALLEL_UPLOADS.update(caps)


def test_register_object_store_registers_and_builds_via_registry() -> None:
    """A backend appears in the registry and builds back through it."""
    # Arrange: a community module registers its factory under its name.
    register_object_store(FAKE_PROVIDER, lambda _cfg: FakeObjectStore())
    config = RemoteSyncConfig(bucket="fake-bucket", provider=FAKE_PROVIDER)

    # Act: the engine builds it the way every surface does.
    store = build_object_store(config)

    # Assert: it came back through the registry, unchanged by the factory.
    assert isinstance(store, FakeObjectStore)
    assert store.describe() == "fake://bucket"
    assert FAKE_PROVIDER in registered_providers()


def test_unregister_object_store_removes_the_provider() -> None:
    """Unregister restores the pre-registration state; unknown names fail closed."""
    # Arrange: register then drop it.
    register_object_store(FAKE_PROVIDER, lambda _cfg: FakeObjectStore())
    unregister_object_store(FAKE_PROVIDER)

    # Act / Assert: no longer registered, and building fails closed.
    assert FAKE_PROVIDER not in registered_providers()
    with pytest.raises(RemoteSyncConfigError, match="unknown remote-sync provider"):
        build_object_store(RemoteSyncConfig(bucket="fake-bucket", provider=FAKE_PROVIDER))


def test_push_then_pull_round_trips_a_registered_fake_backend(
    home: Path, roots: tuple[SyncRoot, ...], tmp_path: Path
) -> None:
    """The engine pushes and pulls against a registry-built store with temp dirs."""
    # Arrange: machine one registers its backend and uploads.
    register_object_store(FAKE_PROVIDER, lambda _cfg: FakeObjectStore())
    store = build_object_store(RemoteSyncConfig(bucket="fake-bucket", provider=FAKE_PROVIDER))
    push(store, roots=roots)

    # Act: machine two starts empty and pulls through the same backend.
    second = tmp_path / "machine-two"
    second_roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=second / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=second / "memory"),
    )
    report: SyncReport = pull(store, roots=second_roots)

    # Assert: both files round-tripped onto machine two.
    assert sorted(report.downloaded) == ["memory/a-fact.md", "sessions/abc.jsonl"]
    assert (second / "sessions" / "abc.jsonl").read_text(encoding="utf-8") == '{"turn": 1}\n'
    assert (second / "memory" / "a-fact.md").read_text(encoding="utf-8") == "remembered\n"
