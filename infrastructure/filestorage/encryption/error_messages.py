"""Static wording for a remote-sync encryption failure, safe on any surface."""

from __future__ import annotations

from config.constants.filestorage import REMOTE_SYNC_ENV, REMOTE_SYNC_PASSPHRASE_ENV
from infrastructure.filestorage.errors import (
    EncryptedStoreError,
    EncryptionDisabledError,
    ManifestMissingError,
    MissingPassphraseError,
    PassphraseNotResolvableError,
    PlaintextStoreError,
    RemoteSyncEncryptionError,
    SyncDisabledError,
    UndecryptableObjectError,
    WrongPassphraseError,
)

MANIFEST_GONE = (
    "This store holds encrypted objects but no manifest to open them.\n"
    "The manifest carried the only copy of the keys, so those objects cannot be\n"
    "recovered — and syncing either way now would make things worse.\n"
    "\n"
    "  If the manifest was deleted by mistake, restore it from a bucket version.\n"
    "  Otherwise start the remote over: empty the prefix, then sync again."
)

ENCRYPTED_STORE_MISMATCH = (
    "This store is encrypted, but encryption is switched off on this machine.\n"
    "Syncing now would upload readable history into an encrypted store.\n"
    "\n"
    "  Turn encryption back on:  `opensre remote-sync setup`\n"
    "  Or, to go back to plaintext, empty the prefix and sync again."
)

PLAINTEXT_STORE = (
    "This store already holds unencrypted sessions or memory.\n"
    "Encrypting only new writes would leave those readable.\n"
    "\n"
    "  Seal what is already there:  `opensre remote-sync reencrypt`"
)

SYNC_DISABLED = (
    "Remote sync is off on this machine.\n"
    "\n"
    f"  Set it up first:  `opensre remote-sync setup` or `export {REMOTE_SYNC_ENV}=1`"
)

ENCRYPTION_DISABLED = (
    "Encryption is off on this machine, so there is no key to change.\n"
    "\n"
    "  Turn it on first:  opensre remote-sync setup"
)

MISSING_PASSPHRASE = (
    "This store is encrypted, but no passphrase is available on this machine.\n"
    f"Run `opensre remote-sync setup` in a terminal, or export {REMOTE_SYNC_PASSPHRASE_ENV}."
)

WRONG_PASSPHRASE = (
    "The passphrase on this machine does not open this store's key, so nothing moved.\n"
    f"Export the right one as {REMOTE_SYNC_PASSPHRASE_ENV}, or run `opensre remote-sync setup`."
)

PASSPHRASE_NOT_RESOLVABLE = (
    "The passphrase was set but cannot be read back on this machine, so the next\n"
    f"command would not find it. Export {REMOTE_SYNC_PASSPHRASE_ENV} in every shell that syncs."
)

UNDECRYPTABLE_OBJECT = (
    "An object in the store could not be opened, so nothing was written locally.\n"
    "Run `opensre remote-sync sync` in a terminal to see which object, or read the log."
)

GENERIC_ENCRYPTION_FAILURE = (
    "Remote sync could not settle this store's encryption, so nothing moved.\n"
    "Run the same command in a terminal, or read the log, for the detail."
)

#: Class → copy. Keyed by class rather than raise site because the raise site is
#: where interpolation happens, and this table exists to leave it behind.
_EXTERNAL_MESSAGES: dict[type[RemoteSyncEncryptionError], str] = {
    EncryptedStoreError: ENCRYPTED_STORE_MISMATCH,
    EncryptionDisabledError: ENCRYPTION_DISABLED,
    ManifestMissingError: MANIFEST_GONE,
    MissingPassphraseError: MISSING_PASSPHRASE,
    PassphraseNotResolvableError: PASSPHRASE_NOT_RESOLVABLE,
    PlaintextStoreError: PLAINTEXT_STORE,
    SyncDisabledError: SYNC_DISABLED,
    UndecryptableObjectError: UNDECRYPTABLE_OBJECT,
    WrongPassphraseError: WRONG_PASSPHRASE,
    RemoteSyncEncryptionError: GENERIC_ENCRYPTION_FAILURE,
}


def external_encryption_message(exc: RemoteSyncEncryptionError) -> str:
    """Copy for ``exc`` that any surface, chat included, may show.

    Never derived from ``exc``, so no object key, store setting, or wrapped
    exception travels with it. A subclass with no entry of its own answers with
    its nearest base's copy, which makes a newly added error class redacted by
    default instead of leaking until someone remembers to list it.
    """
    for cls in type(exc).__mro__:
        message = _EXTERNAL_MESSAGES.get(cls)
        if message is not None:
            return message
    return GENERIC_ENCRYPTION_FAILURE


__all__ = [
    "ENCRYPTED_STORE_MISMATCH",
    "ENCRYPTION_DISABLED",
    "GENERIC_ENCRYPTION_FAILURE",
    "MANIFEST_GONE",
    "MISSING_PASSPHRASE",
    "PASSPHRASE_NOT_RESOLVABLE",
    "PLAINTEXT_STORE",
    "SYNC_DISABLED",
    "UNDECRYPTABLE_OBJECT",
    "WRONG_PASSPHRASE",
    "external_encryption_message",
]
