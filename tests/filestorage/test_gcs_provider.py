"""GCS object-store provider: field mapping, etag conversion, error wrapping."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import Forbidden, GoogleAPIError
from google.auth.exceptions import DefaultCredentialsError

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.engine import content_tag, push
from platform.filestorage.enums import SyncRootName
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.providers import gcs
from platform.filestorage.providers.gcs import GCSObjectStore, _content_etag
from platform.filestorage.providers.registry import build_object_store, registered_providers
from platform.filestorage.syncable import SyncRoot

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _b64_md5(data: bytes) -> str:
    """How GCS spells the digest the engine compares in hex."""
    return base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()


class _FakeBlob:
    """Stands in for ``google.cloud.storage.Blob`` on the listing/get/put seams."""

    def __init__(
        self,
        store: dict[str, bytes],
        name: str,
        *,
        size: int | None = None,
        updated: datetime | None = _NOW,
        md5_hash: str | None = None,
        etag: str = "",
    ) -> None:
        self._store = store
        self.name = name
        self.size = size
        self.updated = updated
        self.md5_hash = md5_hash
        self.etag = etag

    def download_as_bytes(self) -> bytes:
        return self._store[self.name]

    def upload_from_string(self, data: bytes) -> None:
        self._store[self.name] = data


class _FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self._store, name)


class _FakeClient:
    """Serves listings with full control over per-blob metadata."""

    def __init__(self, *, blobs: list[_FakeBlob] | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self._blobs = blobs
        self.list_prefixes: list[str] = []

    def list_blobs(self, _bucket: str, *, prefix: str = "") -> list[_FakeBlob]:
        self.list_prefixes.append(prefix)
        if self._blobs is not None:
            return [b for b in self._blobs if b.name.startswith(prefix)]
        return [
            _FakeBlob(self.objects, key, size=len(data), md5_hash=_b64_md5(data))
            for key, data in sorted(self.objects.items())
            if key.startswith(prefix)
        ]

    def bucket(self, _name: str) -> _FakeBucket:
        return _FakeBucket(self.objects)


class _Failing:
    def list_blobs(self, *_args: object, **_kwargs: object) -> list[_FakeBlob]:
        raise Forbidden("denied")

    def bucket(self, *_args: object) -> _FakeBucket:
        raise GoogleAPIError("boom")


@pytest.fixture
def roots(tmp_path: Path) -> tuple[SyncRoot, ...]:
    sessions = tmp_path / "sessions"
    memory = tmp_path / "memory"
    sessions.mkdir()
    memory.mkdir()
    (sessions / "a.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    (memory / "fact.md").write_text("remembered\n", encoding="utf-8")
    return (
        SyncRoot(name=SyncRootName.SESSIONS, path=sessions),
        SyncRoot(name=SyncRootName.MEMORY, path=memory),
    )


def test_describe_shows_the_gs_destination() -> None:
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), client=_FakeClient())
    assert store.describe() == "gs://b/opensre"


def test_list_maps_blob_fields_strips_prefix_and_converts_md5() -> None:
    data = b'{"turn": 1}\n'
    client = _FakeClient()
    client.objects["opensre/sessions/a.jsonl"] = data
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    (obj,) = store.list_objects("")

    assert obj.key == "sessions/a.jsonl"
    assert obj.size == len(data)
    assert obj.last_modified == _NOW
    assert obj.etag == content_tag(data)
    # Bare listing scopes under the configured prefix with a trailing slash, so
    # "opensre" cannot also match "opensre-backup/".
    assert client.list_prefixes == ["opensre/"]


def test_list_passes_a_relative_prefix_through_key_for() -> None:
    client = _FakeClient()
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), client=client)
    store.list_objects("sessions")
    assert client.list_prefixes == ["opensre/sessions"]


def test_etag_comes_from_md5_hash_never_from_the_version_etag() -> None:
    data = b"payload\n"
    version_tag = "CJi0kcLQ0okDEAE="
    blob = _FakeBlob({}, "opensre/sessions/a.jsonl", md5_hash=_b64_md5(data), etag=version_tag)
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), client=_FakeClient(blobs=[blob]))

    (obj,) = store.list_objects("")

    assert obj.etag == content_tag(data)
    assert obj.etag != version_tag


def test_composite_object_without_md5_hash_gets_no_tag() -> None:
    blob = _FakeBlob({}, "opensre/sessions/big.jsonl", md5_hash=None)
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), client=_FakeClient(blobs=[blob]))
    (obj,) = store.list_objects("")
    assert obj.etag == ""


def test_malformed_md5_hash_gets_no_tag() -> None:
    assert _content_etag("x") == ""
    # Valid-alphabet padding with a stray character must not silently decode.
    assert _content_etag("AAAAAAAAAAAAAAAAAAAAAA==!") == ""
    # Well-formed base64 of anything but a 16-byte digest is not an MD5 tag.
    assert _content_etag(base64.b64encode(b"short").decode()) == ""


def test_get_and_put_route_through_the_configured_prefix() -> None:
    client = _FakeClient()
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), client=client)

    store.put_object("sessions/a.jsonl", b"data")
    assert client.objects["opensre/sessions/a.jsonl"] == b"data"
    assert store.get_object("sessions/a.jsonl") == b"data"


def test_gcs_failures_name_their_cause() -> None:
    store = GCSObjectStore(RemoteSyncConfig(bucket="missing"), client=_Failing())
    with pytest.raises(RemoteSyncUnavailableError, match="Forbidden"):
        store.list_objects("")
    with pytest.raises(RemoteSyncUnavailableError, match="GoogleAPIError"):
        store.get_object("k")
    with pytest.raises(RemoteSyncUnavailableError, match="GoogleAPIError"):
        store.put_object("k", b"d")


def test_transfer_layer_errors_are_wrapped_not_leaked() -> None:
    """DataCorruption/InvalidResponse/connection errors bypass GoogleAPIError."""

    class _Flaky:
        def list_blobs(self, *_args: object, **_kwargs: object) -> list[_FakeBlob]:
            raise ConnectionError("connection dropped")

        def bucket(self, *_args: object) -> _FakeBucket:
            raise RuntimeError("checksum mismatch")

    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), client=_Flaky())
    with pytest.raises(RemoteSyncUnavailableError, match="cannot list"):
        store.list_objects("")
    with pytest.raises(RemoteSyncUnavailableError, match="cannot read"):
        store.get_object("k")
    with pytest.raises(RemoteSyncUnavailableError, match="cannot write"):
        store.put_object("k", b"d")


def test_missing_blob_metadata_falls_back_safely() -> None:
    blob = _FakeBlob({}, "opensre/sessions/a.jsonl", size=None, updated=None)
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), client=_FakeClient(blobs=[blob]))
    (obj,) = store.list_objects("")
    assert obj.size == 0
    assert obj.last_modified == datetime.fromtimestamp(0, tz=UTC)


def test_missing_credentials_fail_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> object:
        raise DefaultCredentialsError("no credentials")

    monkeypatch.setattr(gcs, "storage", SimpleNamespace(Client=_raise))
    with pytest.raises(RemoteSyncUnavailableError, match="cannot build a GCS client"):
        GCSObjectStore(RemoteSyncConfig(bucket="b"))


def test_gcs_is_a_registered_built_in_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "gcs" in registered_providers()
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "storage", SimpleNamespace(Client=lambda: fake))
    built = build_object_store(RemoteSyncConfig(bucket="b", provider="gcs"))
    assert isinstance(built, GCSObjectStore)


def test_second_push_reuploads_nothing(roots: tuple[SyncRoot, ...]) -> None:
    """The md5 conversion must hold through the real engine, not just the mapping."""
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), client=_FakeClient())

    first = push(store, roots=roots)
    assert "sessions/a.jsonl" in first.uploaded

    second = push(store, roots=roots)
    assert second.uploaded == []
    assert second.skipped == 2
