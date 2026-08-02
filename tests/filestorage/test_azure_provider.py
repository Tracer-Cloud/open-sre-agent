from __future__ import annotations

from datetime import UTC, datetime

import pytest
from azure.core.exceptions import AzureError

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.providers.azure import AzureBlobObjectStore


def _config(**overrides: object) -> RemoteSyncConfig:
    base: dict[str, object] = {
        "bucket": "opensre-remote-sync",
        "provider": "azure",
        "prefix": "opensre",
        "profile": "testaccount",
    }
    base.update(overrides)
    return RemoteSyncConfig(**base)  # type: ignore[arg-type]


class _FakeDownloader:
    def __init__(self, data: bytes):
        self._data = data

    def readall(self) -> bytes:
        return self._data


class _FakeBlobClient:
    def __init__(self, data: bytes = b""):
        self.data = data

    def download_blob(self) -> _FakeDownloader:
        return _FakeDownloader(self.data)

    def upload_blob(self, data: bytes, overwrite: bool = True) -> None: # noqa: ARG002
        self.data = data


class _FakeBlobProperties:
    def __init__(self, name: str, size: int):
        self.name = name
        self.size = size
        self.last_modified = datetime.now(UTC)
        self.etag = '"fake-etag-123"'


class _FakeContainerClient:
    def __init__(self) -> None:
        self.blobs: list[_FakeBlobProperties] = []

    def list_blobs(self, name_starts_with: str = "") -> list[_FakeBlobProperties]:
        return [b for b in self.blobs if b.name.startswith(name_starts_with)]


class _FakeBlobServiceClient:
    def __init__(self) -> None:
        self.container_client = _FakeContainerClient()
        self.blob_clients: dict[str, _FakeBlobClient] = {}

    def get_container_client(self, container: str) -> _FakeContainerClient: # noqa: ARG002
        return self.container_client

    def get_blob_client(self, container: str, blob: str) -> _FakeBlobClient: # noqa: ARG002
        if blob not in self.blob_clients:
            self.blob_clients[blob] = _FakeBlobClient()
        return self.blob_clients[blob]


# --- Tests ---


def test_azure_missing_account_name_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client construction must fail if the storage account name cannot be determined."""
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
    # Using a config without the `profile` set
    cfg = RemoteSyncConfig(bucket="b", provider="azure", prefix="p")

    with pytest.raises(RemoteSyncUnavailableError, match="Azure storage account name is missing"):
        AzureBlobObjectStore(cfg)


def test_describe_names_account_bucket_and_prefix() -> None:
    """The describe method accurately reflects the Azure hierarchy."""
    store = AzureBlobObjectStore(_config(), client=_FakeBlobServiceClient())
    assert store.describe() == "azure-blob://testaccount/opensre-remote-sync/opensre"


def test_put_list_get_round_trip() -> None:
    """The provider fulfills the ObjectStore contract using the Azure SDK."""
    fake_service = _FakeBlobServiceClient()
    store = AzureBlobObjectStore(_config(), client=fake_service)

    # Put
    payload = b'{"turn": 1}\n'
    store.put_object("sessions/a.jsonl", payload)

    # Manually register the fake blob property so `list_blobs` can find it
    fake_service.container_client.blobs.append(
        _FakeBlobProperties(name="opensre/sessions/a.jsonl", size=len(payload))
    )

    # List (Testing Prefix Handling)
    listing = store.list_objects("")
    assert len(listing) == 1
    assert listing[0].key == "sessions/a.jsonl"  # Provider strips the "opensre/" prefix internally
    assert listing[0].size == len(payload)
    assert listing[0].etag == "fake-etag-123"

    # Get
    assert store.get_object("sessions/a.jsonl") == payload


def test_azure_sdk_errors_are_translated() -> None:
    """SDK errors are wrapped in RemoteSyncUnavailableError to prevent raw stack traces from leaking."""

    class _FailingBlobClient:
        def download_blob(self) -> None:
            raise AzureError("Simulated SDK failure")

        def upload_blob(self, data: bytes, overwrite: bool = True) -> None: # noqa: ARG002
            raise AzureError("Simulated SDK failure")

    class _FailingContainerClient:
        def list_blobs(self, name_starts_with: str = "") -> None: # noqa: ARG002
            raise AzureError("Simulated SDK failure")

    class _FailingServiceClient:
        def get_container_client(self, container: str) -> _FailingContainerClient: # noqa: ARG002
            return _FailingContainerClient()

        def get_blob_client(self, container: str, blob: str) -> _FailingBlobClient: # noqa: ARG002
            return _FailingBlobClient()

    store = AzureBlobObjectStore(_config(), client=_FailingServiceClient())

    with pytest.raises(RemoteSyncUnavailableError) as caught_list:
        store.list_objects("")
    assert "Simulated SDK failure" in str(caught_list.value)
    assert "AzureError" in str(caught_list.value)

    with pytest.raises(RemoteSyncUnavailableError) as caught_get:
        store.get_object("test.json")
    assert "Simulated SDK failure" in str(caught_get.value)

    with pytest.raises(RemoteSyncUnavailableError) as caught_put:
        store.put_object("test.json", b"data")
    assert "Simulated SDK failure" in str(caught_put.value)
