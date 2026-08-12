"""S3-compatible endpoint provider: MinIO / Cloudflare R2 / DigitalOcean Spaces.

An S3-compatible store speaks the same ``ListObjectsV2`` / ``GetObject`` /
``PutObject`` API as AWS S3, but at a custom ``endpoint_url``. The provider
implements :class:`~platform.filestorage.ports.ObjectStore` against boto3's S3
client pointed at that endpoint, registers under ``s3compat``, and the engine
never changes. Tests here use a fake boto3 client — no live cloud, no moto.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from config.constants.filestorage import (
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENDPOINT_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
)
from platform.filestorage.config import RemoteSyncConfig, load_remote_sync_config
from platform.filestorage.engine import push
from platform.filestorage.enums import RemoteSyncField, SyncRootName
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.providers import build_object_store
from platform.filestorage.providers import s3compat as s3compat_module
from platform.filestorage.providers.registry import (
    provider_extra_fields,
    register_object_store,
    registered_providers,
    unregister_object_store,
)
from platform.filestorage.syncable import SyncRoot

_ENDPOINT = "https://minio.example.local"
_BUCKET = "opensre-bucket"
_PREFIX = "opensre"


class _Paginator:
    """Fake ``list_objects_v2`` paginator, seeded with S3-style listing pages."""

    def __init__(self, *, pages: list[dict[str, Any]] | None = None) -> None:
        self.pages = pages if pages is not None else []
        self.calls: list[dict[str, Any]] = []

    def paginate(self, **kwargs: Any) -> Iterator[dict[str, Any]]:
        self.calls.append(kwargs)
        yield from self.pages


class _Body:
    """Mimics boto3's ``StreamingBody``, which exposes ``read()``."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _Client:
    """Fake boto3 S3 client: in-memory objects plus a paginator.

    Mimics the boto3 surface the provider touches: ``get_paginator``,
    ``get_object``, ``put_object``. Real boto3 returns a ``StreamingBody``
    from ``get_object``, so that call yields a body object with ``read()``.
    """

    def __init__(
        self,
        *,
        pages: list[dict[str, Any]] | None = None,
        objects: dict[str, bytes] | None = None,
    ) -> None:
        self.paginator = _Paginator(pages=pages)
        self.objects = objects if objects is not None else {}
        self.put_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "list_objects_v2"
        return self.paginator

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        return {"Body": _Body(self.objects[kwargs["Key"]])}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}


class _Failing:
    """A client whose calls raise the way botocore does on a bad endpoint."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get_paginator(self, _name: str) -> None:
        raise self._exc

    def get_object(self, **_: Any) -> None:
        raise self._exc

    def put_object(self, **_: Any) -> None:
        raise self._exc


class _RecordingClient:
    """Captures the endpoint passed to ``session.client`` for assertions."""

    def __init__(self) -> None:
        self.endpoint_url: str | None = None

    def get_paginator(self, _name: str) -> _Paginator:
        return _Paginator()

    def get_object(self, **_: Any) -> dict[str, Any]:
        return {"Body": b""}

    def put_object(self, **_: Any) -> dict[str, Any]:
        return {}


def _listing(*, key: str, size: int, etag: str = '"abc123"') -> dict[str, Any]:
    return {
        "Key": f"{_PREFIX}/{key}",
        "Size": size,
        "LastModified": datetime.now(tz=UTC),
        "ETag": etag,
    }


@pytest.fixture(autouse=True)
def _restore_registry() -> Iterator[None]:
    """Registrations are process-global, so snapshot and restore every table.

    The registry keeps four parallel dicts (factories, credential hints, setup
    fields, exposure checkers) plus the upload caps. Clearing only one of them
    would leave a built-in half-registered — ``provider_extra_fields`` for a
    name still in ``_REGISTRY`` but missing from ``_EXTRA_FIELDS`` falls back
    to no fields, so setup validation would then reject the provider's own
    fields. Snapshotting all of them keeps the built-ins intact across tests.
    """
    from platform.filestorage.providers import registry as reg

    with reg._REGISTRY_LOCK:
        snap = {
            "_REGISTRY": dict(reg._REGISTRY),
            "_CREDENTIAL_HINTS": dict(reg._CREDENTIAL_HINTS),
            "_EXTRA_FIELDS": dict(reg._EXTRA_FIELDS),
            "_PUBLIC_ACCESS_CHECKERS": dict(reg._PUBLIC_ACCESS_CHECKERS),
            "_MAX_PARALLEL_UPLOADS": dict(reg._MAX_PARALLEL_UPLOADS),
        }
    yield
    with reg._REGISTRY_LOCK:
        reg._REGISTRY.clear()
        reg._CREDENTIAL_HINTS.clear()
        reg._EXTRA_FIELDS.clear()
        reg._PUBLIC_ACCESS_CHECKERS.clear()
        reg._MAX_PARALLEL_UPLOADS.clear()
        for table in (
            "_REGISTRY",
            "_CREDENTIAL_HINTS",
            "_EXTRA_FIELDS",
            "_PUBLIC_ACCESS_CHECKERS",
            "_MAX_PARALLEL_UPLOADS",
        ):
            getattr(reg, table).update(snap[table])


def _store(*, client: Any, endpoint: str = _ENDPOINT) -> Any:
    return s3compat_module.S3CompatObjectStore(
        RemoteSyncConfig(bucket=_BUCKET, provider="s3compat", prefix=_PREFIX, endpoint=endpoint),
        client=client,
    )


def test_describe_shows_the_s3compat_destination() -> None:
    # Arrange
    store = _store(client=_Client())

    # Act / Assert
    assert store.describe() == f"s3compat://{_BUCKET}/{_PREFIX}"


def test_build_client_passes_the_endpoint_to_boto3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: capture the endpoint boto3 receives.
    captured: dict[str, Any] = {}
    recorded = _RecordingClient()

    def _fake_build_client(config: RemoteSyncConfig) -> Any:
        captured["endpoint"] = config.endpoint
        return recorded

    monkeypatch.setattr(s3compat_module, "_build_client", _fake_build_client)

    # Act
    store = _store(client=None)

    # Assert: the provider would hand the endpoint to session.client("s3", ...).
    assert captured["endpoint"] == _ENDPOINT
    assert store.describe().startswith("s3compat://")


def test_list_objects_delegates_to_s3_list_objects_v2() -> None:
    # Arrange: the endpoint lists two objects under the configured prefix.
    client = _Client(pages=[{"Contents": [_listing(key="sessions/a.jsonl", size=10)]}])
    store = _store(client=client)

    # Act
    objects = store.list_objects("sessions")

    # Assert: paginated under the full prefix and returned relative to it.
    assert client.paginator.calls[0]["Bucket"] == _BUCKET
    assert client.paginator.calls[0]["Prefix"] == f"{_PREFIX}/sessions"
    assert len(objects) == 1
    assert objects[0].key == "sessions/a.jsonl"
    assert objects[0].size == 10
    assert objects[0].etag == "abc123"


def test_list_objects_strips_the_configured_prefix_and_passes_through_unknown_keys() -> None:
    """A key already relative to the configured prefix is returned as-is.

    The provider strips the configured ``opensre/`` prefix from keys, and — like
    the AWS backend — passes through anything that is not under it (it never
    deletes remote objects, so a sibling prefix is merely listed, not dropped).
    """
    # Arrange: listing holds one key under the prefix and one from a sibling.
    client = _Client(
        pages=[
            {
                "Contents": [
                    _listing(key="memory/fact.md", size=9),
                    _listing(key="opensre-backup/x.jsonl", size=1),
                ]
            }
        ]
    )
    store = _store(client=client)

    # Act
    objects = store.list_objects("")

    # Assert: prefix stripped from the real key; sibling key passes through.
    assert sorted(obj.key for obj in objects) == ["memory/fact.md", "opensre-backup/x.jsonl"]


def test_get_object_reads_the_body() -> None:
    # Arrange
    body = b'{"turn": 1}\n'
    client = _Client(objects={f"{_PREFIX}/sessions/a.jsonl": body})
    store = _store(client=client)

    # Act
    data = store.get_object("sessions/a.jsonl")

    # Assert
    assert data == body
    assert client.get_calls[0]["Bucket"] == _BUCKET
    assert client.get_calls[0]["Key"] == f"{_PREFIX}/sessions/a.jsonl"


def test_put_object_writes_without_server_side_encryption() -> None:
    """MinIO / R2 reject the SSE param the AWS backend sends, so it is omitted."""
    # Arrange
    client = _Client()
    store = _store(client=client)

    # Act
    store.put_object("sessions/a.jsonl", b'{"turn": 1}\n')

    # Assert: written under the full key, and no SSE kwarg reached the client.
    assert client.objects[f"{_PREFIX}/sessions/a.jsonl"] == b'{"turn": 1}\n'
    assert "ServerSideEncryption" not in client.put_calls[0]


def test_errors_are_wrapped_naming_the_aws_cause() -> None:
    # Arrange
    client = _Failing(
        ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "The bucket does not exist"}},
            "ListObjectsV2",
        )
    )
    store = _store(client=client)

    # Act / Assert: the AWS-side reason survives, not just a generic message.
    with pytest.raises(RemoteSyncUnavailableError, match="NoSuchBucket"):
        store.list_objects("")
    with pytest.raises(RemoteSyncUnavailableError, match="NoSuchBucket"):
        store.get_object("sessions/a.jsonl")
    with pytest.raises(RemoteSyncUnavailableError, match="NoSuchBucket"):
        store.put_object("sessions/a.jsonl", b"data")


def test_s3compat_is_registered_and_builds_via_registry() -> None:
    # Arrange: load the built-in lazily, exactly as build_object_store does.
    assert "s3compat" in registered_providers()

    # Act
    store = build_object_store(
        RemoteSyncConfig(bucket=_BUCKET, provider="s3compat", endpoint=_ENDPOINT)
    )

    # Assert
    assert isinstance(store, s3compat_module.S3CompatObjectStore)
    assert store.describe().startswith("s3compat://")


def test_s3compat_declares_endpoint_profile_and_region_setup_fields() -> None:
    # Act
    fields = {extra.field for extra in provider_extra_fields("s3compat")}

    # Assert: endpoint plus the profile/region slots boto3 consumes.
    assert fields == {
        RemoteSyncField.ENDPOINT,
        RemoteSyncField.PROFILE,
        RemoteSyncField.REGION,
    }


def test_endpoint_config_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, _BUCKET)
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "s3compat")
    monkeypatch.setenv(REMOTE_SYNC_ENDPOINT_ENV, _ENDPOINT)

    # Act
    config = load_remote_sync_config()

    # Assert
    assert config is not None
    assert config.provider == "s3compat"
    assert config.endpoint == _ENDPOINT


def test_unregister_restores_pre_registration_state() -> None:
    # Arrange: register a stand-in under a throwaway name, then drop it.
    register_object_store("s3compat-tmp", lambda _cfg: _Client())
    assert "s3compat-tmp" in registered_providers()
    unregister_object_store("s3compat-tmp")

    # Act / Assert
    assert "s3compat-tmp" not in registered_providers()


def test_push_through_a_registered_s3compat_store(tmp_path: Path) -> None:
    """The engine pushes against a registry-built store with temp dirs."""
    # Arrange: a laptop tree and a registered fake backend.
    sessions = tmp_path / "sessions"
    memory = tmp_path / "memory"
    sessions.mkdir()
    memory.mkdir()
    (sessions / "a.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    (memory / "fact.md").write_text("remembered\n", encoding="utf-8")
    roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=sessions),
        SyncRoot(name=SyncRootName.MEMORY, path=memory),
    )
    register_object_store("s3compat", lambda _cfg: _store(client=_Client()))
    store = build_object_store(RemoteSyncConfig(bucket=_BUCKET, provider="s3compat"))

    # Act
    report = push(store, roots=roots)

    # Assert: both files uploaded through the endpoint client.
    assert sorted(report.uploaded) == ["memory/fact.md", "sessions/a.jsonl"]
    assert store.describe().startswith("s3compat://")
