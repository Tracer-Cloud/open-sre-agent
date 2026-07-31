"""S3 backend for remote sync — one registered :class:`ObjectStore` implementation."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    ConnectionError as BotoConnectionError,
    EndpointConnectionError,
    ReadTimeoutError,
    UnknownEndpointError,
)

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.enums import BuiltInProvider
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers.registry import register_object_store

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

_SERVER_SIDE_ENCRYPTION = "AES256"
PROVIDER_NAME = BuiltInProvider.AWS

_MAX_RETRY_ATTEMPTS = 3


#BotoCoreError subclasses that represent transient, connection-level failures
_RETRYABLE_BOTOCORE_ERRORS: tuple[type[BotoCoreError], ...] = (
    BotoConnectionError,  
    ConnectTimeoutError,  
    ReadTimeoutError,  
    EndpointConnectionError,  
    UnknownEndpointError,
)

# Service-side throttling/limit codes, 5xx http status codes are the ClientError responses worth
# a retry. Anything else (NoSuchBucket, AccessDenied etc.) is treated as permanent. 
_RETRYABLE_CLIENT_ERROR_CODES = frozenset(
    {
        "SlowDown",#http status code 503
        "RequestTimeout",#http status code 408
        "ServiceUnavailable",#http status code 503
    }
)
#This set of error codes is not exhaustive, but it covers the most common transient errors that can be retried.


def _is_transient(exc: BotoCoreError | ClientError) -> bool:
    """Check whether ``exc`` is worth another attempt; anything else is permanent."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _RETRYABLE_CLIENT_ERROR_CODES:
            return True
        http_status_code = exc.response["ResponseMetadata"]["HTTPStatusCode"]
        return 500<=http_status_code<=504  # 5xx is usually transient
    return isinstance(exc, _RETRYABLE_BOTOCORE_ERRORS)


def _retry_transient[T](fn: Callable[[], T], *, label: str) -> T:
    """Retrying transient S3 failures with exponential backoff."""
    for attempt in range(_MAX_RETRY_ATTEMPTS):
        try:
            return fn()
        except (BotoCoreError, ClientError) as exc:
            if attempt == _MAX_RETRY_ATTEMPTS - 1 or not _is_transient(exc):
                raise
            wait_time = 2**attempt  # 1s, 2s, 4s, ...
            logger.warning(
                "[s3] %s failed (attempt %d/%d), retrying in %ds: %s",
                label,
                attempt + 1,
                _MAX_RETRY_ATTEMPTS,
                wait_time,
                _reason(exc),
            )
            time.sleep(wait_time)
    raise AssertionError("unreachable")  # pragma: no cover


class S3ObjectStore:
    """Reads and writes objects under one bucket and prefix."""

    def __init__(self, config: RemoteSyncConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client if client is not None else _build_client(config)

    def describe(self) -> str:
        return f"s3://{self._config.bucket}/{self._config.prefix}"

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        # Trailing slash so prefix "opensre" cannot also match "opensre-backup/".
        full_prefix = (
            self._config.key_for(prefix) if prefix else f"{self._config.prefix.rstrip('/')}/"
        )
        out: list[RemoteObject] = []
        try:
            for page in self._pages(full_prefix):
                for item in page.get("Contents", []):
                    key = str(item["Key"])
                    out.append(
                        RemoteObject(
                            key=self._strip_prefix(key),
                            size=int(item.get("Size", 0)),
                            last_modified=item["LastModified"],
                            # The listing carries the content tag, so comparing
                            # an object costs no extra request.
                            etag=str(item.get("ETag", "")).strip('"'),
                        )
                    )
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(
                f"cannot list {self.describe()} — {_reason(exc)}"
            ) from exc
        return out

    def get_object(self, key: str) -> bytes:
        def _fetch() -> bytes:
            response = self._client.get_object(
                Bucket=self._config.bucket, Key=self._config.key_for(key)
            )
            body: bytes = response["Body"].read()
            return body

        try:
            return _retry_transient(_fetch, label=f"get_object: {key}")
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(f"cannot read {key} — {_reason(exc)}") from exc

    def put_object(self, key: str, data: bytes) -> None:
        def _write() -> None:
            self._client.put_object(
                Bucket=self._config.bucket,
                Key=self._config.key_for(key),
                Body=data,
                ServerSideEncryption=_SERVER_SIDE_ENCRYPTION,
            )

        try:
            _retry_transient(_write, label=f"put_object: {key}")
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(f"cannot write {key} — {_reason(exc)}") from exc

    def _pages(self, prefix: str) -> Iterator[dict[str, Any]]:
        paginator = self._client.get_paginator("list_objects_v2")
        page_iter = iter(paginator.paginate(Bucket=self._config.bucket, Prefix=prefix))

        def _next_page() -> dict[str, Any]:
            return next(page_iter)

        while True:
            try:
                yield _retry_transient(_next_page, label="list_objects page")
            except StopIteration:
                return

    def _strip_prefix(self, full_key: str) -> str:
        prefix = f"{self._config.prefix.rstrip('/')}/"
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key


def _reason(exc: Exception) -> str:
    """The AWS-side cause, for a local operator to act on."""
    return f"{type(exc).__name__}: {exc}"


def _build_client(config: RemoteSyncConfig) -> Any:
    try:
        # Empty means "use the ambient AWS configuration", which boto3 spells None.
        session = boto3.Session(
            profile_name=config.profile or None,
            region_name=config.region or None,
        )
        return session.client("s3")
    except (BotoCoreError, ClientError, ValueError) as exc:
        raise RemoteSyncUnavailableError(f"cannot build an S3 client — {_reason(exc)}") from exc


def _factory(config: RemoteSyncConfig) -> S3ObjectStore:
    return S3ObjectStore(config)


register_object_store(PROVIDER_NAME, _factory)

__all__ = ["PROVIDER_NAME", "S3ObjectStore"]
