"""Decide whether a run may proceed, and with which cipher.

Every mismatch between "does this machine encrypt" and "is this store encrypted"
fails the run, in both directions. Encrypting over existing readable objects
would report success while leaving them exposed; syncing with encryption off
would push readable history into a store meant to hold none.
"""

from __future__ import annotations

from dataclasses import dataclass

from infrastructure.filestorage.contracts import ObjectStore, RemoteObject
from infrastructure.filestorage.encryption import envelope
from infrastructure.filestorage.encryption.contracts import Cipher
from infrastructure.filestorage.encryption.keys import resolve_passphrase
from infrastructure.filestorage.encryption.manifest import (
    load_manifest,
    manifest_in_listing,
    new_manifest,
    open_manifest,
    save_manifest,
)
from infrastructure.filestorage.enums import SyncRootName
from infrastructure.filestorage.errors import (
    EncryptedStoreError,
    ManifestMissingError,
    PlaintextStoreError,
)

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


_MANIFEST_GONE = (
    "This store holds encrypted objects but no manifest to open them.\n"
    "The manifest carried the only copy of the keys, so those objects cannot be\n"
    "recovered — and syncing either way now would make things worse.\n"
    "\n"
    "  If the manifest was deleted by mistake, restore it from a bucket version.\n"
    "  Otherwise start the remote over: empty the prefix, then sync again."
)


def _holds_sealed_objects(store: ObjectStore, listing: list[RemoteObject]) -> bool:
    """Whether any mirrored object is actually sealed.

    Runs only when there is no manifest to trust: without it a deleted manifest
    makes an encrypted store look plaintext, and the engine writes ciphertext
    over local history. Stops at the first sealed object, so a half-migrated
    prefix counts as encrypted; a plaintext store pays one read per mirrored
    object, since nothing but an object's bytes can answer.
    """
    for obj in listing:
        if obj.key.partition("/")[0] not in _ROOT_HEADS:
            continue
        if envelope.is_sealed(store.get_object(obj.key)):
            return True
    return False


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
                "This store is encrypted, but encryption is switched off on this machine.\n"
                "Syncing now would upload readable history into an encrypted store.\n"
                "\n"
                "  Turn encryption back on:  `opensre remote-sync setup`\n"
                "  Or, to go back to plaintext, empty the prefix and sync again."
            )
        if _holds_sealed_objects(store, listing):
            raise ManifestMissingError(_MANIFEST_GONE)
        return ResolvedCipher(cipher=None, listing=listing)

    passphrase = resolve_passphrase()

    if has_manifest:
        return ResolvedCipher(
            cipher=open_manifest(load_manifest(store), passphrase), listing=listing
        )

    if holds_mirrored_objects(listing):
        if _holds_sealed_objects(store, listing):
            raise ManifestMissingError(_MANIFEST_GONE)
        raise PlaintextStoreError(
            "This store already holds unencrypted sessions or memory.\n"
            "Encrypting only new writes would leave those readable.\n"
            "\n"
            "  Seal what is already there:  `opensre remote-sync reencrypt`"
        )

    manifest, cipher = new_manifest(passphrase)
    if not dry_run:
        save_manifest(store, manifest)
    return ResolvedCipher(cipher=cipher, listing=listing)


__all__ = ["ResolvedCipher", "holds_mirrored_objects", "resolve_cipher"]
