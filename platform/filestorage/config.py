"""Settings for optional remote context sync, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from platform.filestorage.errors import RemoteSyncConfigError

_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class RemoteSyncConfig:
    """Where a laptop mirrors its context, and under which credentials."""

    bucket: str
    prefix: str = DEFAULT_REMOTE_SYNC_PREFIX
    region: str = ""
    profile: str = ""

    def key_for(self, relative_key: str) -> str:
        """Full object key for a path relative to the synced root."""
        return f"{self.prefix.rstrip('/')}/{relative_key.lstrip('/')}"


def remote_sync_enabled() -> bool:
    """Whether the user switched sync on. Off unless explicitly set."""
    return os.getenv(REMOTE_SYNC_ENV, "").strip().lower() in _TRUTHY


def load_remote_sync_config() -> RemoteSyncConfig | None:
    """Settings when sync is on, otherwise ``None``.

    Naming a bucket is not enough — the switch has to be on too, so an
    exported bucket left over from another tool never starts uploading.
    """
    if not remote_sync_enabled():
        return None
    bucket = os.getenv(REMOTE_SYNC_BUCKET_ENV, "").strip()
    if not bucket:
        raise RemoteSyncConfigError(
            f"{REMOTE_SYNC_ENV} is on but {REMOTE_SYNC_BUCKET_ENV} names no bucket"
        )
    prefix = os.getenv(REMOTE_SYNC_PREFIX_ENV, "").strip() or DEFAULT_REMOTE_SYNC_PREFIX
    return RemoteSyncConfig(
        bucket=bucket,
        prefix=prefix,
        region=os.getenv(REMOTE_SYNC_REGION_ENV, "").strip(),
        profile=os.getenv(REMOTE_SYNC_PROFILE_ENV, "").strip(),
    )


__all__ = ["RemoteSyncConfig", "load_remote_sync_config", "remote_sync_enabled"]
