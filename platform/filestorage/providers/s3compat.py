"""S3-compatible endpoint backend (MinIO / Cloudflare R2 / DigitalOcean Spaces).

The S3 API is a de-facto standard: many object stores speak ``ListObjectsV2`` /
``GetObject`` / ``PutObject`` at a custom ``endpoint_url`` even though they are
not AWS. This backend is the same S3 wire protocol as :mod:`aws`, pointed at
``RemoteSyncConfig.endpoint`` instead of the ambient AWS endpoints. It registers
under ``s3compat`` and the engine never changes.

Credentials stay ambient (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``,
profiles, SSO) — opensre does not store them, exactly like the AWS backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.enums import BucketExposure, BuiltInProvider, RemoteSyncField
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.exposure import PublicAccessStatus
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers.registry import SetupExtraField, register_object_store

if TYPE_CHECKING:
    from collections.abc import Iterator

PROVIDER_NAME = BuiltInProvider.S3COMPAT
CREDENTIAL_HINT = "S3-compatible credentials come from the usual places (env, profile, or SSO)."
EXTRA_FIELDS = (
    SetupExtraField(
        RemoteSyncField.ENDPOINT,
        "Endpoint URL (e.g. https://…r2.cloudflarestorage.com; blank for AWS)",
    ),
    SetupExtraField(RemoteSyncField.PROFILE, "Credentials profile (blank if unused)"),
    SetupExtraField(RemoteSyncField.REGION, "Region (blank if unused)"),
)


class S3CompatObjectStore:
    """Reads and writes objects under one bucket and prefix at a custom endpoint."""

    def __init__(self, config: RemoteSyncConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client if client is not None else _build_client(config)

    def describe(self) -> str:
        return f"s3compat://{self._config.bucket}/{self._config.prefix}"

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
        try:
            response = self._client.get_object(
                Bucket=self._config.bucket, Key=self._config.key_for(key)
            )
            body: bytes = response["Body"].read()
            return body
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(f"cannot read {key} — {_reason(exc)}") from exc

    def put_object(self, key: str, data: bytes) -> None:
        try:
            # Unlike AWS S3, MinIO / R2 / Spaces reject the ServerSideEncryption
            # parameter, so it is deliberately not sent here.
            self._client.put_object(
                Bucket=self._config.bucket,
                Key=self._config.key_for(key),
                Body=data,
            )
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(f"cannot write {key} — {_reason(exc)}") from exc

    def _pages(self, prefix: str) -> Iterator[dict[str, Any]]:
        paginator = self._client.get_paginator("list_objects_v2")
        yield from paginator.paginate(Bucket=self._config.bucket, Prefix=prefix)

    def _strip_prefix(self, full_key: str) -> str:
        prefix = f"{self._config.prefix.rstrip('/')}/"
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key


def _reason(exc: Exception) -> str:
    """The provider-side cause, for a local operator to act on."""
    return f"{type(exc).__name__}: {exc}"


def _build_client(config: RemoteSyncConfig) -> Any:
    try:
        # Empty means "use the ambient AWS configuration", which boto3 spells None.
        session = boto3.Session(
            profile_name=config.profile or None,
            region_name=config.region or None,
        )
        return session.client("s3", endpoint_url=config.endpoint or None)
    except (BotoCoreError, ClientError, ValueError) as exc:
        raise RemoteSyncUnavailableError(
            f"cannot build an S3-compatible client — {_reason(exc)}"
        ) from exc


def _factory(config: RemoteSyncConfig) -> S3CompatObjectStore:
    return S3CompatObjectStore(config)


def check_public_access(
    config: RemoteSyncConfig, *, client: Any | None = None
) -> PublicAccessStatus:
    """Ask the endpoint whether ``config.bucket`` is publicly readable.

    Uses ``s3:GetBucketPolicyStatus`` — the same call the AWS backend makes, so
    endpoints that implement it (Cloudflare R2 does) get the same warning.
    Degrades to :class:`~platform.filestorage.enums.BucketExposure.UNKNOWN`
    rather than raising, so a missing permission never blocks ``status`` or
    ``setup``. ``detail`` never carries the raw exception text (CWE-209).
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


# Same rationale as the AWS backend: boto3's low-level client is thread-safe,
# botocore retries throttling itself, and an S3-compatible store behind a
# well-behaved endpoint sustains far more concurrent writes than a laptop's
# session tree will ever produce.
MAX_PARALLEL_UPLOADS = 16

register_object_store(
    PROVIDER_NAME,
    _factory,
    credential_hint=CREDENTIAL_HINT,
    extra_fields=EXTRA_FIELDS,
    public_access_checker=check_public_access,
    max_parallel_uploads=MAX_PARALLEL_UPLOADS,
)

__all__ = [
    "CREDENTIAL_HINT",
    "EXTRA_FIELDS",
    "MAX_PARALLEL_UPLOADS",
    "PROVIDER_NAME",
    "S3CompatObjectStore",
    "check_public_access",
]
