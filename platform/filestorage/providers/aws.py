"""S3 backend for remote sync — one registered :class:`ObjectStore` implementation."""

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

_SERVER_SIDE_ENCRYPTION = "AES256"
PROVIDER_NAME = BuiltInProvider.AWS
CREDENTIAL_HINT = "AWS credentials come from the usual places (env, profile, or SSO)."
EXTRA_FIELDS = (
    SetupExtraField(RemoteSyncField.PROFILE, "Credentials profile (blank if unused)"),
    SetupExtraField(RemoteSyncField.REGION, "Region (blank if unused)"),
)


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
            self._client.put_object(
                Bucket=self._config.bucket,
                Key=self._config.key_for(key),
                Body=data,
                ServerSideEncryption=_SERVER_SIDE_ENCRYPTION,
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


# Above the shared default: the boto3 low-level client is safe to share across
# threads, botocore retries throttling itself, and S3 sustains far more
# concurrent writes per prefix than a laptop's session tree will ever produce.
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
    "S3ObjectStore",
    "check_public_access",
]
