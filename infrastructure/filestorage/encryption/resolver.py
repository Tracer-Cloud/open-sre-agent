"""Decide whether a run may proceed, and with which cipher.

Every mismatch between "does this machine encrypt" and "is this store encrypted"
fails the run, in both directions. Encrypting over existing readable objects
would report success while leaving them exposed; syncing with encryption off
would push readable history into a store meant to hold none.
"""

from __future__ import annotations

from dataclasses import dataclass

from platform.filestorage.encryption import envelope
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
from platform.filestorage.errors import (
    EncryptedStoreError,
    ManifestMissingError,
    PlaintextStoreError,
)
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


_MANIFEST_GONE = (
    "This store holds encrypted objects but no manifest to open them.\n"
    "The manifest carried the only copy of the keys, so those objects cannot be\n"
    "recovered — and syncing either way now would make things worse.\n"
    "\n"
    "  If the manifest was deleted by mistake, restore it from a bucket version.\n"
    "  Otherwise start the remote over: empty the prefix, then sync again."
)


def _holds_sealed_objects(store: ObjectStore, listing: list[RemoteObject]) -> bool:
    """Whether the mirrored objects are actually sealed.

    Costs one ``get_object`` — the smallest mirrored object, so the read stays
    cheap — and only runs when there is no manifest to trust. Without it a
    deleted manifest makes an encrypted store indistinguishable from a plaintext
    one, and the engine writes ciphertext straight over local history.

    One object answers for the store: a mixed prefix can only come from an
    interrupted migration, and either answer routes to the same refusal.
    """
    mirrored = [obj for obj in listing if obj.key.partition("/")[0] in _ROOT_HEADS]
    if not mirrored:
        return False
    smallest = min(mirrored, key=lambda obj: obj.size)
    return envelope.is_sealed(store.get_object(smallest.key))


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
