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
    REMOTE_SYNC_ENCRYPTION_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from platform.filestorage.errors import RemoteSyncConfigError

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class RemoteSyncConfig:
    """Where a laptop mirrors its context, under which backend and credentials.

    ``bucket`` is the top-level store name for the chosen provider (S3 bucket
    or Vercel Blob store name/id; community backends may reuse the field).
    Provider-specific fields (``profile``, ``region``) are ignored by backends
    that do not need them. ``encryption`` records only whether client-side
    encoding is enabled; the ambient passphrase never enters this object.
    """

    bucket: str
    provider: str = DEFAULT_REMOTE_SYNC_PROVIDER
    prefix: str = DEFAULT_REMOTE_SYNC_PREFIX
    region: str = ""
    profile: str = ""
    encryption: bool = False

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


def _env_or_stored_flag(
    env_name: str,
    stored_key: str,
    stored: Callable[[], dict[str, Any]],
) -> bool:
    """Environment boolean, then a valid stored setting, else false.

    A run configured entirely through the environment remains usable when its
    unrelated settings file is damaged. A valid stored encryption opt-in is
    still honored when the other fields happen to come from the environment.
    """
    env = os.getenv(env_name)
    if env is not None and env.strip() != "":
        return _strict_flag(env, source=env_name)
    try:
        value = stored().get(stored_key)
    except RemoteSyncConfigError:
        return False
    if value is None:
        return False
    return _strict_flag(value, source=f"remote_sync.{stored_key}")


def _strict_flag(value: object, *, source: str) -> bool:
    """Parse a security-sensitive boolean without turning typos into false."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSY:
            return False
    raise RemoteSyncConfigError(f"{source} must be one of: 1, 0, true, false, yes, no, on, off")


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
        encryption=_env_or_stored_flag(
            REMOTE_SYNC_ENCRYPTION_ENV,
            "encryption",
            stored,
        ),
    )


__all__ = ["RemoteSyncConfig", "load_remote_sync_config", "remote_sync_enabled"]
