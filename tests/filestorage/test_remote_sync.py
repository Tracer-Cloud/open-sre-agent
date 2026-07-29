"""Remote context sync: opt-in, what moves, and what must never leave the laptop.

The security property under test is that credentials stay local. Sessions and
memory are the only things that mirror; ``integrations.json`` and the model-key
file are excluded by an allowlist of roots and again by name.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config.constants.filestorage import (
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PREFIX_ENV,
)
from platform.filestorage.config import load_remote_sync_config, remote_sync_enabled
from platform.filestorage.errors import RemoteSyncConfigError, UnsyncablePathError
from platform.filestorage.ports import RemoteObject
from platform.filestorage.scope import SyncRoot, is_syncable
from platform.filestorage.sync import file_digest, pull, push, sync

# Planted in the credential files. If sync ever widens, this string shows up in
# an uploaded object and the assertion below fails loudly.
LEAKED_SECRET = "sk-live-CANARY-must-never-reach-the-bucket"


class FakeObjectStore:
    """In-memory ObjectStore, so the engine is testable without AWS."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.digests: dict[str, str] = {}
        self.modified: dict[str, datetime] = {}

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [
            RemoteObject(
                key=key,
                size=len(data),
                last_modified=self.modified.get(key, datetime.now(tz=UTC)),
                digest=self.digests.get(key, ""),
            )
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes, *, digest: str) -> None:
        self.objects[key] = data
        self.digests[key] = digest
        self.modified.setdefault(key, datetime.now(tz=UTC))

    def describe(self) -> str:
        return "fake://bucket"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A laptop ~/.opensre with sessions, memory, and credential files."""
    (tmp_path / "sessions").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / "sessions" / "abc.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    (tmp_path / "memory" / "a-fact.md").write_text("remembered\n", encoding="utf-8")
    # Credentials live beside them and must not move.
    (tmp_path / "integrations.json").write_text(
        f'{{"datadog": {{"api_key": "{LEAKED_SECRET}"}}}}', encoding="utf-8"
    )
    (tmp_path / "llm-auth.json").write_text(f'{{"openai": "{LEAKED_SECRET}"}}', encoding="utf-8")
    return tmp_path


@pytest.fixture
def roots(home: Path) -> tuple[SyncRoot, ...]:
    return (
        SyncRoot(name="sessions", path=home / "sessions"),
        SyncRoot(name="memory", path=home / "memory"),
    )


# ── Opt-in ──────────────────────────────────────────────────────────────────


def test_sync_is_off_unless_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a bucket is named but the switch is not set.
    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "left-over-bucket")

    # Act / Assert: naming a bucket must not start uploading.
    assert remote_sync_enabled() is False
    assert load_remote_sync_config() is None


def test_switched_on_without_a_bucket_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.delenv(REMOTE_SYNC_BUCKET_ENV, raising=False)

    # Act / Assert
    with pytest.raises(RemoteSyncConfigError):
        load_remote_sync_config()


def test_prefix_scopes_the_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv(REMOTE_SYNC_ENV, "yes")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "my-bucket")
    monkeypatch.setenv(REMOTE_SYNC_PREFIX_ENV, "laptop-1")

    # Act
    config = load_remote_sync_config()

    # Assert
    assert config is not None
    assert config.key_for("sessions/abc.jsonl") == "laptop-1/sessions/abc.jsonl"


# ── Credentials never leave the laptop ──────────────────────────────────────


def test_credential_files_are_never_uploaded(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """The canary secret must appear in no uploaded object."""
    # Arrange
    store = FakeObjectStore()

    # Act
    push(store, roots=roots)

    # Assert: something was uploaded, and none of it carries the secret.
    assert store.objects, "expected sessions and memory to upload"
    for key, body in store.objects.items():
        assert LEAKED_SECRET.encode() not in body, f"secret leaked into {key}"
    assert not any("integrations" in key or "llm-auth" in key for key in store.objects)


def test_credential_paths_are_not_syncable(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    # Arrange / Act / Assert
    assert is_syncable(home / "sessions" / "abc.jsonl", roots=roots) is True
    assert is_syncable(home / "memory" / "a-fact.md", roots=roots) is True
    assert is_syncable(home / "integrations.json", roots=roots) is False
    assert is_syncable(home / "llm-auth.json", roots=roots) is False


def test_a_root_pointing_at_credentials_is_refused(home: Path) -> None:
    """Defence in depth: a misconfigured root raises, and leaks nothing first.

    The root allowlist is the primary defence — credential files are normally
    never enumerated. This covers the case where that structure is wrong, which
    is the only way the name check can be reached.
    """
    # Arrange: a root that wrongly covers the whole home directory.
    bad_roots = (SyncRoot(name="everything", path=home),)
    store = FakeObjectStore()

    # Act
    with pytest.raises(UnsyncablePathError):
        push(store, roots=bad_roots)

    # Assert: it stopped before the secret went anywhere.
    for key, body in store.objects.items():
        assert LEAKED_SECRET.encode() not in body, f"secret leaked into {key}"


# ── Moving files ────────────────────────────────────────────────────────────


def test_push_then_pull_restores_a_second_machine(
    home: Path, roots: tuple[SyncRoot, ...], tmp_path: Path
) -> None:
    # Arrange: machine one uploads.
    store = FakeObjectStore()
    push(store, roots=roots)

    # Act: machine two starts empty and pulls.
    second = tmp_path / "machine-two"
    second_roots = (
        SyncRoot(name="sessions", path=second / "sessions"),
        SyncRoot(name="memory", path=second / "memory"),
    )
    report = pull(store, roots=second_roots)

    # Assert
    assert sorted(report.downloaded) == ["memory/a-fact.md", "sessions/abc.jsonl"]
    assert (second / "sessions" / "abc.jsonl").read_text(encoding="utf-8") == '{"turn": 1}\n'
    assert (second / "memory" / "a-fact.md").read_text(encoding="utf-8") == "remembered\n"


def test_unchanged_files_are_skipped_not_reuploaded(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    # Arrange
    store = FakeObjectStore()
    push(store, roots=roots)

    # Act: a second push with nothing changed.
    report = push(store, roots=roots)

    # Assert
    assert report.uploaded == []
    assert report.skipped == 2


def test_newer_remote_wins_over_older_local(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    # Arrange: remote holds a newer edit of a file that also exists locally.
    store = FakeObjectStore()
    newer = b'{"turn": 2}\n'
    store.put_object("sessions/abc.jsonl", newer, digest=file_digest(newer))
    store.modified["sessions/abc.jsonl"] = datetime.now(tz=UTC) + timedelta(hours=1)

    # Act
    pull(store, roots=roots)

    # Assert
    assert (home / "sessions" / "abc.jsonl").read_bytes() == newer


def test_older_remote_does_not_clobber_a_newer_local_edit(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    # Arrange: remote is stale relative to the local file.
    store = FakeObjectStore()
    stale = b'{"turn": 0}\n'
    store.put_object("sessions/abc.jsonl", stale, digest=file_digest(stale))
    store.modified["sessions/abc.jsonl"] = datetime.now(tz=UTC) - timedelta(hours=1)

    # Act
    pull(store, roots=roots)

    # Assert: the local edit survives.
    assert (home / "sessions" / "abc.jsonl").read_bytes() == b'{"turn": 1}\n'


def test_sync_never_deletes(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """A file only one side knows about survives on both."""
    # Arrange
    store = FakeObjectStore()
    only_remote = b"from the other laptop\n"
    store.put_object("memory/other.md", only_remote, digest=file_digest(only_remote))

    # Act
    sync(store, roots=roots)

    # Assert: local-only file still uploaded, remote-only file still present.
    assert (home / "memory" / "other.md").exists()
    assert "memory/a-fact.md" in store.objects
    assert "memory/other.md" in store.objects


def test_a_key_escaping_its_root_is_ignored(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """A hostile key must not write outside the synced directory."""
    # Arrange
    store = FakeObjectStore()
    payload = b"escaped"
    store.put_object("sessions/../../evil.txt", payload, digest=file_digest(payload))

    # Act
    report = pull(store, roots=roots)

    # Assert
    assert report.downloaded == []
    assert not (home.parent / "evil.txt").exists()
