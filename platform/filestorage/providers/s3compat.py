"""S3-compatible backend for remote sync (MinIO, Cloudflare R2, DigitalOcean Spaces).

One registered :class:`~platform.filestorage.ports.ObjectStore` implementation.
Configures path-style addressing and custom endpoint URLs for non-AWS S3 stores.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from config.constants.filestorage import REMOTE_SYNC_ENDPOINT_URL_ENV
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.enums import BucketExposure, BuiltInProvider, RemoteSyncField
from platform.filestorage.errors import RemoteSyncUnavailableError
from platform.filestorage.exposure import PublicAccessStatus
from platform.filestorage.ports import RemoteObject
from platform.filestorage.providers.registry import SetupExtraField, register_object_store

if TYPE_CHECKING:
    from collections.abc import Iterator

PROVIDER_NAME = BuiltInProvider.S3COMPAT
CREDENTIAL_HINT = (
    "S3-compatible credentials come from ambient environment "
    "(AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, profile, or OPENSRE_REMOTE_SYNC_ENDPOINT_URL)."
)
EXTRA_FIELDS = (
    SetupExtraField(RemoteSyncField.PROFILE, "Credentials profile (blank if unused)"),
    SetupExtraField(RemoteSyncField.REGION, "Region (blank if unused)"),
)


class S3CompatObjectStore:
    """Reads and writes objects under one S3-compatible bucket and prefix."""

    def __init__(
        self,
        config: RemoteSyncConfig,
        *,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._endpoint_url = _resolve_endpoint_url(endpoint_url)
        self._client = client if client is not None else _build_client(config, self._endpoint_url)

    def describe(self) -> str:
        return f"s3compat://{self._config.bucket}/{self._config.prefix}"

    def list_objects(self, prefix: str) -> list[RemoteObject]:
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


def _resolve_endpoint_url(explicit: str | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    env_endpoint = (
        os.getenv(REMOTE_SYNC_ENDPOINT_URL_ENV, "").strip()
        or os.getenv("AWS_ENDPOINT_URL_S3", "").strip()
        or os.getenv("AWS_ENDPOINT_URL", "").strip()
    )
    return env_endpoint or None


def _build_client(config: RemoteSyncConfig, endpoint_url: str | None = None) -> Any:
    try:
        resolved_endpoint = _resolve_endpoint_url(endpoint_url)
        session = boto3.Session(
            profile_name=config.profile or None,
            region_name=config.region or None,
        )
        client_config = Config(s3={"addressing_style": "path"})
        return session.client(
            "s3",
            endpoint_url=resolved_endpoint,
            config=client_config,
        )
    except (BotoCoreError, ClientError, ValueError) as exc:
        raise RemoteSyncUnavailableError(
            f"cannot build an S3-compatible client — {_reason(exc)}"
        ) from exc


def _factory(config: RemoteSyncConfig) -> S3CompatObjectStore:
    return S3CompatObjectStore(config)


def check_public_access(
    config: RemoteSyncConfig, *, client: Any | None = None
) -> PublicAccessStatus:
    """Ask the store whether ``config.bucket`` is publicly readable."""
    try:
        s3 = client if client is not None else _build_client(config)
        response = s3.get_bucket_policy_status(Bucket=config.bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "AccessDenied":
            return PublicAccessStatus(
                BucketExposure.UNKNOWN, "missing the s3:GetBucketPolicyStatus permission"
            )
        if code in ("NoSuchBucketPolicy", "MethodNotAllowed", "NotImplemented"):
            return PublicAccessStatus(BucketExposure.PRIVATE)
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    except (BotoCoreError, ValueError, RemoteSyncUnavailableError) as exc:
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    is_public = bool(response.get("PolicyStatus", {}).get("IsPublic", False))
    return PublicAccessStatus(BucketExposure.PUBLIC if is_public else BucketExposure.PRIVATE)


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
