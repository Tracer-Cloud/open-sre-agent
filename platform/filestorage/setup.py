"""Persist remote-sync settings (shared by CLI and slash ``setup``).

Writes the ``remote_sync`` section of ``~/.opensre/config.yml``. Ambient cloud
credentials (AWS profile session, ``BLOB_READ_WRITE_TOKEN``, …) stay outside
this file — opensre never stores them.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.filestorage import (
    BLOB_READ_WRITE_TOKEN_ENV,
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
)
from config.local_settings import LocalSettingsError, update_section
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.enums import BuiltInProvider
from platform.filestorage.errors import RemoteSyncConfigError


@dataclass(frozen=True)
class RemoteSyncSetupRequest:
    """Values to store for the next laptop that loads config from disk."""

    bucket: str
    provider: str = DEFAULT_REMOTE_SYNC_PROVIDER
    prefix: str = DEFAULT_REMOTE_SYNC_PREFIX
    region: str = ""
    profile: str = ""
    enabled: bool = True


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
    try:
        update_section(
            "remote_sync",
            {
                "enabled": bool(request.enabled),
                "provider": provider,
                "bucket": bucket,
                "prefix": prefix,
                "region": region,
                "profile": profile,
            },
        )
    except LocalSettingsError as exc:
        raise RemoteSyncConfigError(str(exc)) from exc
    return RemoteSyncConfig(
        bucket=bucket,
        provider=provider,
        prefix=prefix,
        region=region,
        profile=profile,
    )


def credential_hint_for_provider(provider: str) -> str:
    """One line on ambient credentials for the chosen backend (no secrets)."""
    key = provider.strip().lower()
    if key == BuiltInProvider.VERCEL.value:
        return f"Set {BLOB_READ_WRITE_TOKEN_ENV} in the environment (opensre does not store it)."
    if key == BuiltInProvider.AWS.value:
        return "AWS credentials come from the usual places (env, profile, or SSO)."
    return "Use ambient credentials for this provider; opensre does not store them."


__all__ = [
    "RemoteSyncSetupRequest",
    "credential_hint_for_provider",
    "save_remote_sync_settings",
]
