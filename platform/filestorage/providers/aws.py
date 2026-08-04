"""S3 backend for remote sync — one registered :class:`ObjectStore` implementation."""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.enums import BucketExposure, BuiltInProvider, RemoteSyncField
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.exposure import PublicAccessStatus
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers.registry import SetupExtraField, register_object_store

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

_SERVER_SIDE_ENCRYPTION = "AES256"
PROVIDER_NAME = BuiltInProvider.AWS
CREDENTIAL_HINT = "AWS credentials come from the usual places (env, profile, or SSO)."
EXTRA_FIELDS = (
    SetupExtraField(RemoteSyncField.PROFILE, "Credentials profile (blank if unused)"),
    SetupExtraField(RemoteSyncField.REGION, "Region (blank if unused)"),
)

# Transient-failure retry policy for S3 calls. A dropped connection or a
# throttling response should not fail the whole run, so retryable errors back
# off and retry; permanent errors (see PERMANENT_ERROR_CODES) never do.
RETRY_MAX_ATTEMPTS = 4
RETRY_BASE_DELAY_SECONDS = 0.5
RETRY_BACKOFF_FACTOR = 2.0

# Error codes S3 returns that are the caller's fault, not a blip: retrying only
# burns time and quota, so these surface immediately.
PERMANENT_ERROR_CODES = frozenset(
    {
        "NoSuchBucket",
        "AccessDenied",
        "NoSuchKey",
        "NoSuchBucketPolicy",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
    }
)
# BotoCoreError subclasses that are a misconfiguration, not a blip: missing or
# incomplete credentials never resolve by retrying, so they surface at once
# rather than after four futile attempts and 3.5s of backoff.
PERMANENT_BOTOCORE_ERRORS: tuple[type[BotoCoreError], ...] = (
    NoCredentialsError,
    PartialCredentialsError,
)
# Codes that mean "the store was busy or briefly unhealthy" — safe to retry.
TRANSIENT_ERROR_CODES = frozenset(
    {
        "SlowDown",
        "RequestTimeout",
        "RequestTimeoutException",
        "Throttling",
        "ThrottlingException",
        "RequestLimitExceeded",
        "ServiceUnavailable",
        "InternalError",
    }
)


def _is_transient(exc: BotoCoreError | ClientError) -> bool:
    """Whether ``exc`` is a blip worth retrying rather than a permanent fault.

    A :class:`BotoCoreError` is usually a client-side network/connection
    failure (no HTTP response reached us), which is transient — except the
    credential-configuration subclasses in :data:`PERMANENT_BOTOCORE_ERRORS`,
    which never resolve by retrying. A :class:`ClientError` is a real S3
    response: retry only throttling codes and 5xx statuses, and never the
    permanent set (bad bucket, denied, missing key).
    """
    if isinstance(exc, PERMANENT_BOTOCORE_ERRORS):
        return False
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", ""))
        if code in PERMANENT_ERROR_CODES:
            return False
        if code in TRANSIENT_ERROR_CODES:
            return True
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return int(status or 0) >= HTTPStatus.INTERNAL_SERVER_ERROR
    # BotoCoreError: a connection dropped, timed out, or never resolved.
    return True


def _with_retry[T](call: Callable[[], T], *, sleep: Callable[[float], None] | None = None) -> T:
    """Run ``call``, retrying transient S3 failures with exponential backoff.

    Permanent failures (and any non-boto exception) propagate on the first hit.
    Transient failures back off up to ``RETRY_MAX_ATTEMPTS`` times; the last
    failure is re-raised so the caller can wrap it in
    :class:`RemoteSyncUnavailableError`. ``sleep`` defaults to
    :func:`time.sleep`, resolved lazily so a test patching ``aws.time.sleep``
    takes effect without threading an argument through every call site.
    """
    if sleep is None:
        sleep = time.sleep
    delay = RETRY_BASE_DELAY_SECONDS
    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            return call()
        except (BotoCoreError, ClientError) as exc:
            if not _is_transient(exc) or attempt == RETRY_MAX_ATTEMPTS:
                raise
            # Detail stays server-side only (CWE-209): callers map the final
            # raise to a generic RemoteSyncUnavailableError.
            logger.warning(
                "[remote-sync] transient S3 failure (attempt %d/%d), retrying in %.1fs: %s",
                attempt,
                RETRY_MAX_ATTEMPTS,
                delay,
                _reason(exc),
            )
            sleep(delay)
            delay *= RETRY_BACKOFF_FACTOR
    # Unreachable: the loop either returns or raises on every path, but a bare
    # fall-through would silently return None, so fail loudly instead.
    raise AssertionError("retry loop exited without returning or raising")


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
            # list() so pagination runs *inside* _with_retry: a transient blip
            # partway through a multi-page listing is then retried. Returning
            # the bare generator would defer pagination to the loop below,
            # outside the retry wrapper, and lose that coverage.
            for page in _with_retry(lambda: list(self._pages(full_prefix))):
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
        def _read() -> bytes:
            response = self._client.get_object(
                Bucket=self._config.bucket, Key=self._config.key_for(key)
            )
            body: bytes = response["Body"].read()
            return body

        try:
            return _with_retry(_read)
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
            _with_retry(_write)
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(f"cannot write {key} — {_reason(exc)}") from exc

    def _pages(self, prefix: str) -> Iterator[dict[str, Any]]:
        paginator = self._client.get_paginator("list_objects_v2")
        yield from paginator.paginate(Bucket=self._config.bucket, Prefix=prefix)

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


def check_public_access(
    config: RemoteSyncConfig, *, client: Any | None = None
) -> PublicAccessStatus:
    """Ask S3 whether ``config.bucket`` is publicly readable.

    Uses ``s3:GetBucketPolicyStatus``, so the result reflects only the bucket
    policy (plus account/bucket Block Public Access settings, which factor
    into ``IsPublic``), not object or bucket ACLs.

    Degrades to :class:`~platform.filestorage.enums.BucketExposure.UNKNOWN`
    rather than raising: the permission can be missing on an otherwise
    perfectly usable bucket, and this check must never stand in the way of
    ``status`` or ``setup`` reporting the rest of what they know. ``detail``
    never carries the raw exception text for any failure but the permission
    gap — this result can be echoed straight into a gateway chat surface (see
    ``format_status_lines``), and an S3 error message is not vetted for that
    (CWE-209).
    """
    try:
        s3 = client if client is not None else _build_client(config)
        response = s3.get_bucket_policy_status(Bucket=config.bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "AccessDenied":
            return PublicAccessStatus(
                BucketExposure.UNKNOWN, "missing the s3:GetBucketPolicyStatus permission"
            )
        if code == "NoSuchBucketPolicy":
            # No policy means nothing grants public access through one.
            return PublicAccessStatus(BucketExposure.PRIVATE)
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    except (BotoCoreError, ValueError, RemoteSyncUnavailableError) as exc:
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    is_public = bool(response.get("PolicyStatus", {}).get("IsPublic", False))
    return PublicAccessStatus(BucketExposure.PUBLIC if is_public else BucketExposure.PRIVATE)


register_object_store(
    PROVIDER_NAME,
    _factory,
    credential_hint=CREDENTIAL_HINT,
    extra_fields=EXTRA_FIELDS,
    public_access_checker=check_public_access,
)

__all__ = [
    "CREDENTIAL_HINT",
    "EXTRA_FIELDS",
    "PROVIDER_NAME",
    "S3ObjectStore",
    "check_public_access",
]
