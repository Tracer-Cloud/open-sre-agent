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


class PassphraseNotResolvableError(RemoteSyncEncryptionError):
    """A passphrase was accepted somewhere it matters, but will not resolve here.

    Raised by the persist step rather than the next command that fails, because
    the two are far apart: after a rotation the store already expects the new
    passphrase, so a machine that cannot read it back is stranded, and the only
    useful moment to say so is while the operator still has it to hand.
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


class SyncDisabledError(RemoteSyncEncryptionError):
    """A key operation was asked for on a machine where remote sync is off.

    There is no store to hold a key, so this is a setup problem rather than a
    key problem — kept apart from the mismatch errors, which describe a store
    that exists and disagrees with this machine.
    """


class EncryptionDisabledError(RemoteSyncEncryptionError):
    """A key operation was asked for while encryption is off on this machine.

    Distinct from :class:`EncryptedStoreError`: nothing about the store is
    wrong, this machine simply has no key to rotate or reseal under.
    """


class EncryptedStoreError(RemoteSyncEncryptionError):
    """The store is encrypted but this machine has encryption switched off.

    The mirror image of :class:`PlaintextStoreError`, and the direction that
    would push readable history into a store meant to hold none.
    """


__all__ = [
    "EncryptedStoreError",
    "EncryptionDisabledError",
    "ManifestMissingError",
    "MissingPassphraseError",
    "OrgScopeNotSupportedError",
    "PassphraseNotResolvableError",
    "PlaintextStoreError",
    "RemoteSyncConfigError",
    "RemoteSyncEncryptionError",
    "RemoteSyncError",
    "RemoteSyncUnavailableError",
    "SyncDisabledError",
    "UndecryptableObjectError",
    "UnsyncablePathError",
    "WrongPassphraseError",
]
