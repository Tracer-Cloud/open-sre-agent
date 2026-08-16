"""Decide whether a run may proceed, and with which cipher.

Every combination of "is this machine configured to encrypt" and "is this store
encrypted" is answered here, and every mismatch fails the run. The two that
matter are opposites of each other:

* encryption on, store already holds readable objects — refuse, because a run
  that quietly sealed new writes would report success while the existing
  history stayed exposed;
* encryption off, store is encrypted — refuse, because this is the path that
  would push readable history into a store whose purpose was to hold none.

Neither is recoverable by guessing, so both name the command that fixes them.
"""

from __future__ import annotations

from dataclasses import dataclass

from platform.filestorage.encryption.keys import resolve_passphrase
from platform.filestorage.encryption.manifest import (
    load_manifest,
    manifest_in_listing,
    new_manifest,
    open_manifest,
    save_manifest,
)
from platform.filestorage.encryption.ports import Cipher
from platform.filestorage.enums import SyncRootName
from platform.filestorage.errors import EncryptedStoreError, PlaintextStoreError
from platform.filestorage.ports import ObjectStore, RemoteObject

_ROOT_HEADS = frozenset(root.value for root in SyncRootName)


@dataclass(frozen=True)
class ResolvedCipher:
    """The cipher for this run, plus the listing already fetched to decide it.

    The listing is handed back so the engine does not pay for a second one: the
    gate has to read the store before any transfer, and that is the same call
    the sync itself would have made.
    """

    cipher: Cipher | None
    listing: list[RemoteObject]


def holds_mirrored_objects(listing: list[RemoteObject]) -> bool:
    """Whether the store already holds sessions or memory.

    Only the mirrored roots count. Another tool's objects sharing the prefix are
    not this feature's business and must not decide whether a sync may run.
    """
    return any(obj.key.partition("/")[0] in _ROOT_HEADS for obj in listing)


def resolve_cipher(store: ObjectStore, *, encrypted: bool, dry_run: bool = False) -> ResolvedCipher:
    """Check the store against this machine's setting and build the cipher.

    Creates the manifest when encryption is switched on for an empty store,
    except under ``dry_run`` — a preview writes nothing anywhere, so it plans
    against a throwaway key instead.
    """
    listing = store.list_objects("")
    has_manifest = manifest_in_listing(listing)

    if not encrypted:
        if has_manifest:
            raise EncryptedStoreError(
                "this store is encrypted, but encryption is switched off on this machine. "
                "Turn it back on (`opensre remote-sync setup`) — syncing without "
                "it would upload readable history into an encrypted store."
                "To upload readable history, delete the manifest and re-run with encryption off"
            )
        return ResolvedCipher(cipher=None, listing=listing)

    passphrase = resolve_passphrase()

    if has_manifest:
        return ResolvedCipher(
            cipher=open_manifest(load_manifest(store), passphrase), listing=listing
        )

    if holds_mirrored_objects(listing):
        raise PlaintextStoreError(
            "this store already holds unencrypted sessions or memory, so encrypting only "
            "new writes would leave them readable. Run `opensre remote-sync reencrypt` to "
            "seal what is already there, then delete the old plaintext objects yourself — "
            "sync never deletes."
        )

    manifest, cipher = new_manifest(passphrase)
    if not dry_run:
        save_manifest(store, manifest)
    return ResolvedCipher(cipher=cipher, listing=listing)


__all__ = ["ResolvedCipher", "holds_mirrored_objects", "resolve_cipher"]
