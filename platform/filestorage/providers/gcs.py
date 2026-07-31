"""GCS backend for remote sync — one registered :class:`ObjectStore` implementation."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any

from google.cloud import storage

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.enums import BuiltInProvider
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers.registry import register_object_store

# Every operation catches broad ``Exception``, not ``GoogleAPIError``: the
# transfer layer (google-resumable-media / requests) raises DataCorruption,
# InvalidResponse, and raw connection errors that do not inherit it, and the
# engine may only ever see RemoteSyncError from a provider.
PROVIDER_NAME = BuiltInProvider.GCS

CREDENTIAL_HINT = (
    "GCP credentials stay ambient: run `gcloud auth application-default login` "
    "once per machine with the account that owns the bucket; opensre stores nothing."
)

_EPOCH = datetime.fromtimestamp(0, tz=UTC)


class GCSObjectStore:
    """Reads and writes objects under one bucket and prefix."""

    def __init__(self, config: RemoteSyncConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client if client is not None else _build_client()

    def describe(self) -> str:
        return f"gs://{self._config.bucket}/{self._config.prefix}"

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        # Trailing slash so prefix "opensre" cannot also match "opensre-backup/".
        full_prefix = (
            self._config.key_for(prefix) if prefix else f"{self._config.prefix.rstrip('/')}/"
        )
        try:
            # Materialize inside the try: the iterator does I/O lazily.
            blobs = list(self._client.list_blobs(self._config.bucket, prefix=full_prefix))
        except Exception as exc:
            raise RemoteSyncUnavailableError(
                f"cannot list {self.describe()} — {_reason(exc)}"
            ) from exc
        out: list[RemoteObject] = []
        for blob in blobs:
            out.append(
                RemoteObject(
                    key=self._strip_prefix(blob.name),
                    size=blob.size or 0,
                    # A listing always carries ``updated``; the epoch fallback
                    # only guards a malformed resource and reads as "oldest",
                    # so it can never clobber a newer copy on either side.
                    last_modified=blob.updated or _EPOCH,
                    # md5Hash rides the listing, so comparing an object costs
                    # no extra request. ``blob.etag`` is a version tag, not a
                    # content hash, and is never used here.
                    etag=_content_etag(blob.md5_hash),
                )
            )
        return out

    def get_object(self, key: str) -> bytes:
        try:
            blob = self._client.bucket(self._config.bucket).blob(self._config.key_for(key))
            body: bytes = blob.download_as_bytes()
            return body
        except Exception as exc:
            raise RemoteSyncUnavailableError(f"cannot read {key} — {_reason(exc)}") from exc

    def put_object(self, key: str, data: bytes) -> None:
        try:
            blob = self._client.bucket(self._config.bucket).blob(self._config.key_for(key))
            blob.upload_from_string(data)
        except Exception as exc:
            raise RemoteSyncUnavailableError(f"cannot write {key} — {_reason(exc)}") from exc

    def _strip_prefix(self, full_key: str) -> str:
        prefix = f"{self._config.prefix.rstrip('/')}/"
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key


def _content_etag(md5_hash: str | None) -> str:
    """Hex MD5 comparable with the engine's content tag, or empty when unknown.

    GCS base64-encodes the digest; the engine compares lowercase hex. Composite
    objects carry no ``md5Hash`` at all, and a foreign object could carry a
    malformed one — both get no tag, so the engine falls back to timestamps,
    the same degradation S3 multipart ETags get. A real digest is exactly 16
    bytes; ``validate=True`` keeps stray characters from silently decoding.
    """
    if not md5_hash:
        return ""
    try:
        digest = base64.b64decode(md5_hash, validate=True)
    except (binascii.Error, ValueError):
        return ""
    return digest.hex() if len(digest) == 16 else ""


def _reason(exc: Exception) -> str:
    """The GCS-side cause, for a local operator to act on."""
    return f"{type(exc).__name__}: {exc}"


def _build_client() -> Any:
    try:
        # No arguments means Application Default Credentials.
        return storage.Client()
    except Exception as exc:
        raise RemoteSyncUnavailableError(f"cannot build a GCS client — {_reason(exc)}") from exc


def _factory(config: RemoteSyncConfig) -> GCSObjectStore:
    return GCSObjectStore(config)


register_object_store(PROVIDER_NAME, _factory)

__all__ = ["CREDENTIAL_HINT", "PROVIDER_NAME", "GCSObjectStore"]
