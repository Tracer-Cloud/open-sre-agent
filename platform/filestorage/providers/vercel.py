"""Vercel Blob backend for remote sync — one registered :class:`ObjectStore`."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx

from config.constants.filestorage import BLOB_READ_WRITE_TOKEN_ENV
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.enums import BuiltInProvider
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers.registry import register_object_store

if TYPE_CHECKING:
    from collections.abc import Iterator

PROVIDER_NAME = BuiltInProvider.VERCEL

# Official control-plane URL used by @vercel/blob (list/put).
_API_BASE = "https://vercel.com/api/blob"
_API_VERSION = "12"
_ACCESS = "private"
_LIST_LIMIT = 1000
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class VercelBlobObjectStore:
    """Reads and writes private blobs under one store and prefix.

    Auth is ambient ``BLOB_READ_WRITE_TOKEN`` (same env Vercel documents), not
    stored by opensre. ``RemoteSyncConfig.bucket`` is the store name/id used only
    in human-facing ``describe()`` output.
    """

    def __init__(
        self,
        config: RemoteSyncConfig,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        resolved = (token if token is not None else _token_from_env()).strip()
        if not resolved:
            raise RemoteSyncUnavailableError(
                f"cannot build a Vercel Blob client — set {BLOB_READ_WRITE_TOKEN_ENV}"
            )
        self._token = resolved
        self._store_id = _store_id_from_token(resolved)
        if not self._store_id:
            raise RemoteSyncUnavailableError(
                "cannot build a Vercel Blob client — token has no store id"
            )
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT)
        self._owns_client = client is None

    def describe(self) -> str:
        return f"vercel-blob://{self._config.bucket}/{self._config.prefix}"

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        # Trailing slash so prefix "opensre" cannot also match "opensre-backup/".
        full_prefix = (
            self._config.key_for(prefix) if prefix else f"{self._config.prefix.rstrip('/')}/"
        )
        out: list[RemoteObject] = []
        try:
            for blob in self._iter_blobs(full_prefix):
                pathname = str(blob.get("pathname", ""))
                out.append(
                    RemoteObject(
                        key=self._strip_prefix(pathname),
                        size=int(blob.get("size", 0)),
                        last_modified=_parse_uploaded_at(blob.get("uploadedAt")),
                        etag=str(blob.get("etag", "")).strip('"'),
                    )
                )
        except httpx.HTTPError as exc:
            raise RemoteSyncUnavailableError(
                f"cannot list {self.describe()} — {_reason(exc)}"
            ) from exc
        return out

    def get_object(self, key: str) -> bytes:
        pathname = self._config.key_for(key)
        url = _private_blob_url(self._store_id, pathname)
        try:
            response = self._client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as exc:
            raise RemoteSyncUnavailableError(f"cannot read {key} — {_reason(exc)}") from exc

    def put_object(self, key: str, data: bytes) -> None:
        pathname = self._config.key_for(key)
        params = urlencode({"pathname": pathname})
        headers = {
            **self._auth_headers(),
            "x-vercel-blob-access": _ACCESS,
            "x-allow-overwrite": "true",
            "x-add-random-suffix": "false",
            "x-content-type": "application/octet-stream",
        }
        try:
            response = self._client.put(
                f"{_API_BASE}?{params}",
                content=data,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RemoteSyncUnavailableError(f"cannot write {key} — {_reason(exc)}") from exc

    def _iter_blobs(self, prefix: str) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {
                "prefix": prefix,
                "limit": _LIST_LIMIT,
                "mode": "expanded",
            }
            if cursor:
                params["cursor"] = cursor
            response = self._client.get(
                _API_BASE,
                params=params,
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            payload = response.json()
            for blob in payload.get("blobs", []) or []:
                if isinstance(blob, dict):
                    yield blob
            if not payload.get("hasMore"):
                break
            next_cursor = payload.get("cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._token}",
            "x-api-version": _API_VERSION,
        }

    def _strip_prefix(self, full_key: str) -> str:
        prefix = f"{self._config.prefix.rstrip('/')}/"
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key


def _token_from_env() -> str:
    return os.getenv(BLOB_READ_WRITE_TOKEN_ENV, "")


def _store_id_from_token(token: str) -> str:
    """Store id embedded in ``vercel_blob_rw_<storeId>_<secret>`` tokens.

    Matches ``parseStoreIdFromReadWriteToken`` in ``@vercel/blob``.
    """
    parts = token.split("_")
    if len(parts) < 4:
        return ""
    return parts[3]


def _private_blob_url(store_id: str, pathname: str) -> str:
    return f"https://{store_id}.private.blob.vercel-storage.com/{pathname.lstrip('/')}"


def _parse_uploaded_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        # API returns ISO-8601 with Z suffix.
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return datetime.now(tz=UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _reason(exc: Exception) -> str:
    """The Vercel-side cause, for a local operator to act on."""
    if isinstance(exc, httpx.HTTPStatusError):
        detail = ""
        try:
            body = exc.response.json()
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict) and err.get("message"):
                    detail = f": {err['message']}"
                elif body.get("message"):
                    detail = f": {body['message']}"
        except (ValueError, TypeError):
            detail = ""
        return f"HTTP {exc.response.status_code}{detail}"
    return f"{type(exc).__name__}: {exc}"


def _factory(config: RemoteSyncConfig) -> VercelBlobObjectStore:
    return VercelBlobObjectStore(config)


register_object_store(PROVIDER_NAME, _factory)

__all__ = ["PROVIDER_NAME", "VercelBlobObjectStore"]
