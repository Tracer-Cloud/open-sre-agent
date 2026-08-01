"""Client-side encryption contracts for remote-sync content."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.constants.filestorage import REMOTE_SYNC_PASSPHRASE_ENV
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.encryption import content_codec_for
from platform.filestorage.engine import content_tag, pull, push
from platform.filestorage.errors import (
    RemoteSyncConfigError,
    RemoteSyncEncryptionError,
)
from platform.filestorage.ports import RemoteObject
from platform.filestorage.syncable import SyncRoot

_PASSPHRASE = "correct horse battery staple"
_OBJECT_KEY = "sessions/incident.jsonl"
_PLAINTEXT = b'{"incident": "database latency"}\n'


class _MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.last_modified = datetime(2020, 1, 1, tzinfo=UTC)

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [
            RemoteObject(
                key=key,
                size=len(data),
                last_modified=self.last_modified,
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
        return "memory://encrypted-sync"


def _encrypted_config() -> RemoteSyncConfig:
    return RemoteSyncConfig(
        bucket="incident-history",
        provider="memory",
        prefix="opensre",
        encryption=True,
    )


def test_encrypted_codec_is_deterministic_authenticated_and_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, _PASSPHRASE)
    codec = content_codec_for(_encrypted_config())

    first = codec.encode(_OBJECT_KEY, _PLAINTEXT)
    second = codec.encode(_OBJECT_KEY, _PLAINTEXT)
    another_path = codec.encode("memory/incident.jsonl", _PLAINTEXT)

    assert first == second
    assert first != another_path
    assert first != _PLAINTEXT
    assert _PLAINTEXT not in first
    assert codec.decode(_OBJECT_KEY, first) == _PLAINTEXT


@pytest.mark.parametrize("failure", ["wrong_passphrase", "wrong_key", "tampered", "plaintext"])
def test_encrypted_codec_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, _PASSPHRASE)
    codec = content_codec_for(_encrypted_config())
    encoded = codec.encode(_OBJECT_KEY, _PLAINTEXT)

    if failure == "wrong_passphrase":
        monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, "not-the-passphrase")
        failing_codec = content_codec_for(_encrypted_config())
        key, payload = _OBJECT_KEY, encoded
    elif failure == "wrong_key":
        failing_codec = codec
        key, payload = "memory/incident.jsonl", encoded
    elif failure == "tampered":
        failing_codec = codec
        key, payload = _OBJECT_KEY, encoded[:-1] + bytes([encoded[-1] ^ 1])
    else:
        failing_codec = codec
        key, payload = _OBJECT_KEY, _PLAINTEXT

    with pytest.raises(RemoteSyncEncryptionError):
        failing_codec.decode(key, payload)


def test_encryption_requires_an_ambient_passphrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REMOTE_SYNC_PASSPHRASE_ENV, raising=False)

    with pytest.raises(RemoteSyncConfigError, match=REMOTE_SYNC_PASSPHRASE_ENV):
        content_codec_for(_encrypted_config())


def test_encryption_rejects_a_whitespace_only_passphrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, "   ")

    with pytest.raises(RemoteSyncConfigError, match=REMOTE_SYNC_PASSPHRASE_ENV):
        content_codec_for(_encrypted_config())


def test_encrypted_sync_preserves_change_detection_and_restores_plaintext(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, _PASSPHRASE)
    codec = content_codec_for(_encrypted_config())
    source = tmp_path / "source" / "sessions"
    source.mkdir(parents=True)
    source_file = source / "incident.jsonl"
    source_file.write_bytes(_PLAINTEXT)
    source_roots = (SyncRoot(name="sessions", path=source),)
    store = _MemoryStore()

    first = push(store, roots=source_roots, codec=codec)
    second = push(store, roots=source_roots, codec=codec)

    assert first.uploaded == [_OBJECT_KEY]
    assert store.objects[_OBJECT_KEY] != _PLAINTEXT
    assert second.uploaded == []
    assert second.skipped == 1

    restored = tmp_path / "restored" / "sessions"
    restored_roots = (SyncRoot(name="sessions", path=restored),)
    report = pull(store, roots=restored_roots, codec=codec)

    assert report.downloaded == [_OBJECT_KEY]
    assert (restored / "incident.jsonl").read_bytes() == _PLAINTEXT


def test_shared_remote_sync_service_applies_configured_encryption(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from platform.filestorage import operations

    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, _PASSPHRASE)
    source = tmp_path / "sessions"
    source.mkdir()
    (source / "incident.jsonl").write_bytes(_PLAINTEXT)
    roots = (SyncRoot(name="sessions", path=source),)
    store = _MemoryStore()
    monkeypatch.setattr(operations, "load_remote_sync_config", _encrypted_config)
    monkeypatch.setattr(operations, "syncable_roots", lambda: roots)
    monkeypatch.setattr(operations, "build_object_store", lambda _config: store)

    report = operations.run_remote_sync(push_only=True)
    first_objects = dict(store.objects)
    second = operations.run_remote_sync(push_only=True)

    assert report is not None
    assert report.uploaded == [_OBJECT_KEY]
    assert store.objects[_OBJECT_KEY] != _PLAINTEXT
    assert _PLAINTEXT not in store.objects[_OBJECT_KEY]
    assert second is not None
    assert second.uploaded == []
    assert second.skipped == 1
    assert store.objects == first_objects


def test_shared_service_rejects_wrong_passphrase_before_upload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from platform.filestorage import operations

    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, _PASSPHRASE)
    source = tmp_path / "sessions"
    source.mkdir()
    source_file = source / "incident.jsonl"
    source_file.write_bytes(_PLAINTEXT)
    roots = (SyncRoot(name="sessions", path=source),)
    store = _MemoryStore()
    monkeypatch.setattr(operations, "load_remote_sync_config", _encrypted_config)
    monkeypatch.setattr(operations, "syncable_roots", lambda: roots)
    monkeypatch.setattr(operations, "build_object_store", lambda _config: store)
    operations.run_remote_sync(push_only=True)
    original_objects = dict(store.objects)

    source_file.write_bytes(b'{"incident": "new local evidence"}\n')
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, "wrong-passphrase")

    with pytest.raises(RemoteSyncEncryptionError):
        operations.run_remote_sync(push_only=True)

    assert store.objects == original_objects


def test_shared_service_rejects_a_nonempty_unverified_prefix_before_upload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from platform.filestorage import operations

    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, _PASSPHRASE)
    source = tmp_path / "sessions"
    source.mkdir()
    (source / "incident.jsonl").write_bytes(_PLAINTEXT)
    roots = (SyncRoot(name="sessions", path=source),)
    store = _MemoryStore()
    store.objects[_OBJECT_KEY] = b"legacy plaintext"
    original_objects = dict(store.objects)
    monkeypatch.setattr(operations, "load_remote_sync_config", _encrypted_config)
    monkeypatch.setattr(operations, "syncable_roots", lambda: roots)
    monkeypatch.setattr(operations, "build_object_store", lambda _config: store)

    with pytest.raises(RemoteSyncEncryptionError):
        operations.run_remote_sync(push_only=True)

    assert store.objects == original_objects


def test_shared_service_refuses_to_disable_encryption_on_an_initialized_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from platform.filestorage import operations

    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, _PASSPHRASE)
    source = tmp_path / "sessions"
    source.mkdir()
    source_file = source / "incident.jsonl"
    source_file.write_bytes(_PLAINTEXT)
    roots = (SyncRoot(name="sessions", path=source),)
    store = _MemoryStore()
    monkeypatch.setattr(operations, "load_remote_sync_config", _encrypted_config)
    monkeypatch.setattr(operations, "syncable_roots", lambda: roots)
    monkeypatch.setattr(operations, "build_object_store", lambda _config: store)
    operations.run_remote_sync(push_only=True)
    original_objects = dict(store.objects)

    source_file.write_bytes(b'{"incident": "unencrypted overwrite"}\n')
    monkeypatch.setattr(
        operations,
        "load_remote_sync_config",
        lambda: RemoteSyncConfig(
            bucket="incident-history",
            provider="memory",
            prefix="opensre",
            encryption=False,
        ),
    )

    with pytest.raises(RemoteSyncEncryptionError):
        operations.run_remote_sync(push_only=True)

    assert store.objects == original_objects


def test_encrypted_pull_only_keeps_an_empty_prefix_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from platform.filestorage import operations

    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, _PASSPHRASE)
    roots = (SyncRoot(name="sessions", path=tmp_path / "sessions"),)
    store = _MemoryStore()
    monkeypatch.setattr(operations, "load_remote_sync_config", _encrypted_config)
    monkeypatch.setattr(operations, "syncable_roots", lambda: roots)
    monkeypatch.setattr(operations, "build_object_store", lambda _config: store)

    report = operations.run_remote_sync(pull_only=True)

    assert report is not None
    assert report.changed == 0
    assert store.objects == {}
