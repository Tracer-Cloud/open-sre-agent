"""Errors raised while mirroring context to a user-owned bucket."""

from __future__ import annotations


class RemoteSyncError(RuntimeError):
    """Base for every remote-sync failure."""


class RemoteSyncConfigError(RemoteSyncError):
    """Sync is switched on but the settings are unusable."""


class RemoteSyncUnavailableError(RemoteSyncError):
    """The bucket could not be reached or the credentials were rejected."""


class UnsyncablePathError(RemoteSyncError):
    """A path outside the syncable roots was offered for upload.

    Raised rather than skipped: reaching this means a caller tried to mirror
    something the user did not agree to share, and silence would hide it.
    """


__all__ = [
    "RemoteSyncConfigError",
    "RemoteSyncError",
    "RemoteSyncUnavailableError",
    "UnsyncablePathError",
]
