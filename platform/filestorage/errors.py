"""Errors raised while mirroring context to a user-owned bucket."""

from __future__ import annotations


class RemoteSyncError(RuntimeError):
    """Base for every remote-sync failure."""


class RemoteSyncConfigError(RemoteSyncError):
    """Sync is switched on but the settings are unusable."""


class RemoteSyncUnavailableError(RemoteSyncError):
    """The bucket could not be reached or the credentials were rejected."""


class OrgScopeNotSupportedError(RemoteSyncError):
    """Raised when an organization-scoped turn asks for remote sync.

    Bucket keys are not namespaced by principal or actor, so two members of one
    organization would share every key and read each other's conversations.
    Organization data already persists through the mounted context root, so this
    fails closed rather than mirroring anything.
    """


class UnsyncablePathError(RemoteSyncError):
    """A path outside the syncable roots was offered for upload.

    Raised rather than skipped: reaching this means a caller tried to mirror
    something the user did not agree to share, and silence would hide it.
    """


class RemoteSyncEncryptionError(RemoteSyncError):
    """Base for every client-side encryption failure.

    Every subclass fails the run closed. Encryption exists so the store never
    holds readable history, and a degraded mode that uploads plaintext when the
    key is unavailable would defeat it silently — the one outcome the feature
    must never produce.
    """


class MissingPassphraseError(RemoteSyncEncryptionError):
    """Encryption is on but no passphrase could be resolved on this machine."""


class WrongPassphraseError(RemoteSyncEncryptionError):
    """The passphrase did not unwrap the store's key.

    Indistinguishable from a tampered manifest by design: both mean this
    machine cannot speak for this store, and neither may proceed.
    """


class UndecryptableObjectError(RemoteSyncEncryptionError):
    """A stored object could not be opened.

    Raised before the local file is touched. The engine resolves a conflict by
    recency, so an unreadable object that happens to be newer would otherwise
    overwrite good local history with bytes nobody can read.
    """


class PlaintextStoreError(RemoteSyncEncryptionError):
    """Encryption is on, but the store already holds unencrypted objects.

    Mixing is refused rather than migrated silently: the plaintext copies stay
    readable to the store's operator, and a run that quietly left them there
    would report success while the history it was meant to protect was still
    exposed.
    """


class EncryptedStoreError(RemoteSyncEncryptionError):
    """The store is encrypted but this machine has encryption switched off.

    The mirror image of :class:`PlaintextStoreError`, and the more dangerous
    direction: without this check, turning encryption off would push readable
    history into a store whose whole point was that it held none.
    """


__all__ = [
    "EncryptedStoreError",
    "MissingPassphraseError",
    "OrgScopeNotSupportedError",
    "PlaintextStoreError",
    "RemoteSyncConfigError",
    "RemoteSyncEncryptionError",
    "RemoteSyncError",
    "RemoteSyncUnavailableError",
    "UndecryptableObjectError",
    "UnsyncablePathError",
    "WrongPassphraseError",
]
