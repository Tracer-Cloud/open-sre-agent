from __future__ import annotations

import os
from typing import Any, cast

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.enums import BuiltInProvider, RemoteSyncField
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers import SetupExtraField
from platform.filestorage.providers.registry import register_object_store

PROVIDER_NAME = BuiltInProvider.AZURE
CREDENTIAL_HINT = "Azure credentials come from the ambient environment (e.g., Azure CLI, managed identity, or env vars)."
EXTRA_FIELDS = (
    SetupExtraField(RemoteSyncField.PROFILE, "Azure Storage Account Name (e.g., opensre)"),
)


class AzureBlobObjectStore:
    """Reads and writes objects under an Azure Storage Container and prefix."""

    def __init__(self, config: RemoteSyncConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client if client is not None else _build_client(config)

    def describe(self) -> str:
        account_name = self._config.profile or os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "unknown")
        return f"azure-blob://{account_name}/{self._config.bucket}/{self._config.prefix}"

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        # Append trailing slash to avoid matching "opensre-backup/" when prefix is "opensre"
        full_prefix = (
            self._config.key_for(prefix) if prefix else f"{self._config.prefix.rstrip('/')}/"
        )
        out: list[RemoteObject] = []

        try:
            container_client = self._client.get_container_client(self._config.bucket)
            for blob in container_client.list_blobs(name_starts_with=full_prefix):
                out.append(
                    RemoteObject(
                        key=self._strip_prefix(blob.name),
                        size=blob.size or 0,
                        last_modified=blob.last_modified,
                        etag=str(blob.etag).strip('"') if blob.etag else "",
                    )
                )
        except AzureError as exc:
            raise RemoteSyncUnavailableError(
                f"Cannot list {self.describe()} - {_reason(exc)}"
            ) from exc

        return out

    def get_object(self, key: str) -> bytes:
        try:
            blob_client = self._client.get_blob_client(
                container=self._config.bucket, blob=self._config.key_for(key)
            )
            data = blob_client.download_blob().readall()
            return cast(bytes, data)
        except AzureError as exc:
            raise RemoteSyncUnavailableError(f"Cannot read {key} - {_reason(exc)}") from exc

    def put_object(self, key: str, data: bytes) -> None:
        try:
            blob_client = self._client.get_blob_client(
                container=self._config.bucket, blob=self._config.key_for(key)
            )
            blob_client.upload_blob(data, overwrite=True)
        except AzureError as exc:
            raise RemoteSyncUnavailableError(f"Cannot write {key} - {_reason(exc)}") from exc

    def _strip_prefix(self, full_key: str) -> str:
        prefix = f"{self._config.prefix.rstrip('/')}/"
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key


def _reason(exc: Exception) -> str:
    """The Azure-side cause, formatted safely for a local operator to act on."""
    return f"{type(exc).__name__}: {exc}"


def _build_client(config: RemoteSyncConfig) -> Any:
    account_name = config.profile or os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")
    if not account_name:
        raise RemoteSyncUnavailableError(
            "Azure storage account name is missing. Set it during setup or via AZURE_STORAGE_ACCOUNT_NAME."
        )

    account_url = f"https://{account_name}.blob.core.windows.net"
    try:
        return BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
    except Exception as exc:
        raise RemoteSyncUnavailableError(f"Cannot build Azure client - {_reason(exc)}") from exc


def _factory(config: RemoteSyncConfig) -> AzureBlobObjectStore:
    return AzureBlobObjectStore(config)


register_object_store(
    PROVIDER_NAME, _factory, credential_hint=CREDENTIAL_HINT, extra_fields=EXTRA_FIELDS
)

__all__ = ["CREDENTIAL_HINT", "EXTRA_FIELDS", "PROVIDER_NAME", "AzureBlobObjectStore"]
