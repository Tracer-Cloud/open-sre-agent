"""Settings for optional remote context sync.

Environment variables take precedence over ``~/.opensre/config.yml`` section
``remote_sync``, so a one-off export can redirect a single run without editing
the file. Naming a bucket alone never enables sync — the switch must be on in
env or in the stored section.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from platform.filestorage.errors import RemoteSyncConfigError

_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class RemoteSyncConfig:
    """Where a laptop mirrors its context, under which backend and credentials.

    ``bucket`` is the top-level store name for the chosen provider (S3 bucket
    today; community backends may reuse the field). Provider-specific fields
    (``profile``, ``region``) are ignored by backends that do not need them.
    """

    bucket: str
    provider: str = DEFAULT_REMOTE_SYNC_PROVIDER
    prefix: str = DEFAULT_REMOTE_SYNC_PREFIX
    region: str = ""
    profile: str = ""

    def key_for(self, relative_key: str) -> str:
        """Full object key for a path relative to the synced root."""
        return f"{self.prefix.rstrip('/')}/{relative_key.lstrip('/')}"


def _stored_section() -> dict[str, Any]:
    """Stored settings, or a RemoteSyncError when the file cannot be read.

    A damaged ``config.yml`` must not crash a status check: surfaces catch
    RemoteSyncError, so the failure is translated here rather than escaping as
    an unrelated exception type.
    """
    from config.local_settings import LocalSettingsError, read_section

    try:
        return read_section("remote_sync")
    except LocalSettingsError as exc:
        raise RemoteSyncConfigError(str(exc)) from exc


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return False


def _env_or_stored(
    env_name: str,
    stored_key: str,
    stored: Callable[[], dict[str, Any]],
) -> str:
    """Environment value, else the stored one. The file is read only if needed."""
    env = os.getenv(env_name, "").strip()
    if env:
        return env
    value = stored().get(stored_key)
    if value is None:
        return ""
    return str(value).strip()


def remote_sync_enabled() -> bool:
    """Whether sync is on in the environment or the stored settings file."""
    env = os.getenv(REMOTE_SYNC_ENV)
    if env is not None and env.strip() != "":
        return env.strip().lower() in _TRUTHY
    return _truthy(_stored_section().get("enabled"))


def _lazy_stored() -> Callable[[], dict[str, Any]]:
    """Read the settings file at most once, and only if something needs it.

    A run configured entirely through the environment must not fail because an
    unrelated section of ``config.yml`` is damaged.
    """
    cache: dict[str, dict[str, Any]] = {}

    def read() -> dict[str, Any]:
        if "section" not in cache:
            cache["section"] = _stored_section()
        return cache["section"]

    return read


def load_remote_sync_config() -> RemoteSyncConfig | None:
    """Settings when sync is on, otherwise ``None``.

    Naming a bucket is not enough — the switch has to be on too, so an
    exported bucket left over from another tool never starts uploading.
    """
    if not remote_sync_enabled():
        return None
    stored = _lazy_stored()
    bucket = _env_or_stored(REMOTE_SYNC_BUCKET_ENV, "bucket", stored)
    if not bucket:
        raise RemoteSyncConfigError(
            f"{REMOTE_SYNC_ENV} is on but {REMOTE_SYNC_BUCKET_ENV} names no bucket"
        )
    prefix = _env_or_stored(REMOTE_SYNC_PREFIX_ENV, "prefix", stored) or DEFAULT_REMOTE_SYNC_PREFIX
    provider = (
        _env_or_stored(REMOTE_SYNC_PROVIDER_ENV, "provider", stored).lower()
        or DEFAULT_REMOTE_SYNC_PROVIDER
    )
    return RemoteSyncConfig(
        bucket=bucket,
        provider=provider,
        prefix=prefix,
        region=_env_or_stored(REMOTE_SYNC_REGION_ENV, "region", stored),
        profile=_env_or_stored(REMOTE_SYNC_PROFILE_ENV, "profile", stored),
    )


__all__ = ["RemoteSyncConfig", "load_remote_sync_config", "remote_sync_enabled"]
