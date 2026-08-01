"""GCS backend for remote sync — one registered :class:`ObjectStore` implementation.

Talks to the GCS JSON API over google-auth's ``AuthorizedSession`` directly.
google-auth is already a hard dependency, so a bearer-token vendor earns no SDK
of its own — same plain-HTTP shape as the Vercel provider.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import google.auth
import requests
from google.auth.transport.requests import AuthorizedSession

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.enums import BuiltInProvider
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers.registry import register_object_store

PROVIDER_NAME = BuiltInProvider.GCS

CREDENTIAL_HINT = (
    "GCP credentials stay ambient: run `gcloud auth application-default login` "
    "once per machine with the account that owns the bucket; opensre stores nothing."
)

_API_BASE = "https://storage.googleapis.com/storage/v1"
_UPLOAD_BASE = "https://storage.googleapis.com/upload/storage/v1"
_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
_LIST_FIELDS = "items(name,size,updated,md5Hash),nextPageToken"
_TIMEOUT = 30.0

_EPOCH = datetime.fromtimestamp(0, tz=UTC)


class GCSObjectStore:
    """Reads and writes objects under one bucket and prefix."""

    def __init__(self, config: RemoteSyncConfig, *, session: Any | None = None) -> None:
        self._config = config
        self._session = session if session is not None else _build_session()

    def describe(self) -> str:
        return f"gs://{self._config.bucket}/{self._config.prefix}"

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        # Trailing slash so prefix "opensre" cannot also match "opensre-backup/".
        full_prefix = (
            self._config.key_for(prefix) if prefix else f"{self._config.prefix.rstrip('/')}/"
        )
        try:
            items = self._list_all(full_prefix)
        except (requests.RequestException, TypeError, ValueError) as exc:
            # Transport failures and malformed success payloads both map to
            # unavailable, so the operator gets an actionable error, not a crash.
            raise RemoteSyncUnavailableError(
                f"cannot list {self.describe()} — {_reason(exc)}"
            ) from exc
        out: list[RemoteObject] = []
        for item in items:
            out.append(
                RemoteObject(
                    key=self._strip_prefix(str(item["name"])),
                    size=int(item.get("size", "0")),
                    last_modified=_parse_updated(item.get("updated")),
                    # md5Hash rides the listing, so comparing an object costs no
                    # extra request. The resource ``etag`` is a generation tag,
                    # not a content hash, and is never used here.
                    etag=_content_etag(item.get("md5Hash")),
                )
            )
        return out

    def get_object(self, key: str) -> bytes:
        name = quote(self._config.key_for(key), safe="")
        try:
            response = self._session.get(
                f"{_API_BASE}/b/{self._config.bucket}/o/{name}",
                params={"alt": "media"},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            body: bytes = response.content
            return body
        except requests.RequestException as exc:
            raise RemoteSyncUnavailableError(f"cannot read {key} — {_reason(exc)}") from exc

    def put_object(self, key: str, data: bytes) -> None:
        try:
            response = self._session.post(
                f"{_UPLOAD_BASE}/b/{self._config.bucket}/o",
                params={"uploadType": "media", "name": self._config.key_for(key)},
                data=data,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RemoteSyncUnavailableError(f"cannot write {key} — {_reason(exc)}") from exc

    def _list_all(self, full_prefix: str) -> list[dict[str, Any]]:
        """Every object resource under ``full_prefix``, across pages."""
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params = {"prefix": full_prefix, "fields": _LIST_FIELDS}
            if page_token:
                params["pageToken"] = page_token
            response = self._session.get(
                f"{_API_BASE}/b/{self._config.bucket}/o", params=params, timeout=_TIMEOUT
            )
            response.raise_for_status()
            payload = response.json()
            items.extend(payload.get("items", []))
            page_token = payload.get("nextPageToken", "")
            if not page_token:
                return items

    def _strip_prefix(self, full_key: str) -> str:
        prefix = f"{self._config.prefix.rstrip('/')}/"
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key


def _parse_updated(value: Any) -> datetime:
    """RFC 3339 listing timestamp; a malformed or missing one reads as "oldest"."""
    if not isinstance(value, str) or not value:
        return _EPOCH
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return _EPOCH


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


def _build_session() -> Any:
    try:
        # google.auth.default resolves Application Default Credentials;
        # AuthorizedSession attaches and refreshes the bearer token per call.
        credentials, _project = google.auth.default(scopes=[_SCOPE])
        return AuthorizedSession(credentials)
    except Exception as exc:
        raise RemoteSyncUnavailableError(f"cannot build GCS credentials — {_reason(exc)}") from exc


def _factory(config: RemoteSyncConfig) -> GCSObjectStore:
    return GCSObjectStore(config)


register_object_store(PROVIDER_NAME, _factory, credential_hint=CREDENTIAL_HINT)

__all__ = ["CREDENTIAL_HINT", "PROVIDER_NAME", "GCSObjectStore"]
