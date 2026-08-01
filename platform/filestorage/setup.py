"""Persist remote-sync settings (shared by CLI and slash ``setup``).

Writes the ``remote_sync`` section of ``~/.opensre/config.yml``. Ambient cloud
credentials (AWS profile session, ``BLOB_READ_WRITE_TOKEN``, …) and the
client-side encryption passphrase stay outside this file — opensre never stores
them.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
)
from config.local_settings import LocalSettingsError, update_section
from platform.filestorage.config import (
    RemoteSyncConfig,
    load_stored_encryption_setting,
)
from platform.filestorage.errors import RemoteSyncConfigError


@dataclass(frozen=True)
class RemoteSyncSetupRequest:
    """Values to store for the next laptop that loads config from disk.

    ``encryption=None`` preserves the stored mode. Callers must pass an
    explicit boolean to change the confidentiality boundary.
    """

    bucket: str
    provider: str = DEFAULT_REMOTE_SYNC_PROVIDER
    prefix: str = DEFAULT_REMOTE_SYNC_PREFIX
    region: str = ""
    profile: str = ""
    enabled: bool = True
    encryption: bool | None = None


def save_remote_sync_settings(request: RemoteSyncSetupRequest) -> RemoteSyncConfig:
    """Merge ``request`` into ``remote_sync`` and return the stored config shape.

    Does not enable process env overrides — those still win at load time.
    """
    bucket = request.bucket.strip()
    if not bucket:
        raise RemoteSyncConfigError("bucket (store name) is required")
    provider = request.provider.strip().lower() or DEFAULT_REMOTE_SYNC_PROVIDER
    prefix = request.prefix.strip() or DEFAULT_REMOTE_SYNC_PREFIX
    region = request.region.strip()
    profile = request.profile.strip()
    encryption = (
        request.encryption if request.encryption is not None else load_stored_encryption_setting()
    )
    values: dict[str, object] = {
        "enabled": bool(request.enabled),
        "provider": provider,
        "bucket": bucket,
        "prefix": prefix,
        "region": region,
        "profile": profile,
    }
    if request.encryption is not None:
        values["encryption"] = request.encryption
    try:
        update_section("remote_sync", values)
    except LocalSettingsError as exc:
        raise RemoteSyncConfigError(str(exc)) from exc
    return RemoteSyncConfig(
        bucket=bucket,
        provider=provider,
        prefix=prefix,
        region=region,
        profile=profile,
        encryption=encryption,
    )


__all__ = [
    "RemoteSyncSetupRequest",
    "save_remote_sync_settings",
]
