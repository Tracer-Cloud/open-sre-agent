"""Persist remote-sync settings (``opensre remote-sync setup``).

Writes the ``remote_sync`` section of ``~/.opensre/config.yml``. Ambient cloud
credentials (AWS profile/SSO session, GCP Application Default Credentials)
stay outside this file — opensre never stores them. ``update_section`` merges,
so keys written by older versions survive; setup itself supplies exactly the
six values below and never anything credential-shaped.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
)
from config.local_settings import LocalSettingsError, update_section
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.errors import RemoteSyncConfigError
from platform.filestorage.providers.registry import registered_providers


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

    Environment overrides still win at load time. The provider is validated
    against the registry so a typo fails here, not at the first sync.
    """
    bucket = request.bucket.strip()
    if not bucket:
        raise RemoteSyncConfigError("bucket (store name) is required")
    provider = request.provider.strip().lower() or DEFAULT_REMOTE_SYNC_PROVIDER
    known = registered_providers()
    if provider not in known:
        listed = ", ".join(known) or "(none)"
        raise RemoteSyncConfigError(f"unknown remote-sync provider {provider!r}; known: {listed}")
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


__all__ = ["RemoteSyncSetupRequest", "save_remote_sync_settings"]
