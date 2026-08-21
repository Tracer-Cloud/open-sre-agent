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

    Every subclass fails the run closed: a degraded mode that uploaded plaintext
    when the key was unavailable would silently defeat the feature.
    """


class MissingPassphraseError(RemoteSyncEncryptionError):
    """Encryption is on but no passphrase could be resolved on this machine."""


class WrongPassphraseError(RemoteSyncEncryptionError):
    """The passphrase did not unwrap the store's key.

    Indistinguishable from a tampered manifest by design — both mean this
    machine cannot speak for this store.
    """


class UndecryptableObjectError(RemoteSyncEncryptionError):
    """A stored object could not be opened.

    Raised before the local file is touched: conflicts resolve by recency, so an
    unreadable newer object would otherwise overwrite good local history.
    """


class PlaintextStoreError(RemoteSyncEncryptionError):
    """Encryption is on, but the store already holds unencrypted objects.

    Refused rather than migrated silently, which would report success while the
    existing readable copies stayed exposed.
    """


class ManifestMissingError(RemoteSyncEncryptionError):
    """The store holds sealed objects but no manifest to open them.

    Almost always a deleted manifest, and the keys it carried are gone with it.
    Refused rather than guessed at from either side: with encryption off the
    engine would write sealed bytes over local sessions, and with it on the
    store looks like plaintext and invites a re-encrypt that cannot decrypt
    anything.
    """


class EncryptedStoreError(RemoteSyncEncryptionError):
    """The store is encrypted but this machine has encryption switched off.

    The mirror image of :class:`PlaintextStoreError`, and the direction that
    would push readable history into a store meant to hold none.
    """


__all__ = [
    "EncryptedStoreError",
    "ManifestMissingError",
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
