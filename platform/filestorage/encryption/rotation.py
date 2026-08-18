"""Changing the passphrase, and re-keying the objects themselves.

Two very different operations, and the distinction is the point of the two-level
key design:

**Rotating the passphrase** re-wraps the existing content keys. It touches one
small object, finishes instantly however large the store is, and immediately
stops the old passphrase from opening anything.

**Re-encrypting** mints a new content key and rewrites every object under it, at
the cost of a full upload. Each object is overwritten at its own key, so the
previous ciphertext is replaced rather than accumulating — with one exception
worth stating plainly: on a **versioned** bucket the superseded versions remain,
and they still open under the old key. Removing noncurrent versions is the
operator's job; this never deletes anything.

Re-encrypting is also the migration path for a store that predates encryption,
so it accepts objects that are not sealed at all and seals them.
"""

from __future__ import annotations

from dataclasses import dataclass

from platform.filestorage.encryption import envelope
from platform.filestorage.encryption.cipher import ManifestCipher
from platform.filestorage.encryption.keys import forget_cached_kek
from platform.filestorage.encryption.manifest import (
    MANIFEST_KEY,
    load_manifest,
    manifest_in_listing,
    new_manifest,
    open_manifest,
    rewrapped,
    save_manifest,
    with_new_generation,
)
from platform.filestorage.encryption.resolver import holds_mirrored_objects
from platform.filestorage.errors import RemoteSyncEncryptionError
from platform.filestorage.ports import ObjectStore


@dataclass(frozen=True)
class ReencryptReport:
    """What a re-encrypt rewrote."""

    resealed: list[str]
    #: Objects that had never been encrypted — the migration case, and the
    #: number worth showing an operator adopting encryption on an old store.
    adopted: int = 0
    already_current: int = 0

    @property
    def changed(self) -> int:
        return len(self.resealed)


def rotate_passphrase(store: ObjectStore, *, old_passphrase: str, new_passphrase: str) -> None:
    """Re-wrap the store's keys under ``new_passphrase``. No object is rewritten.

    Persisting the new passphrase is the caller's job — it is the surface that
    asked for it, and this layer writing secrets as a side effect would hide a
    failure to store one behind an operation that appeared to succeed. The
    cached derivation *is* dropped here, because it is keyed by the old salt
    and a stale hit would return a KEK that no longer opens anything.
    """
    listing = store.list_objects("")
    if not manifest_in_listing(listing):
        raise RemoteSyncEncryptionError(
            "this store is not encrypted, so there is no passphrase to rotate. "
            "Run `opensre remote-sync setup` first."
        )
    updated = rewrapped(load_manifest(store), old_passphrase, new_passphrase)
    save_manifest(store, updated)
    forget_cached_kek()


def reencrypt(store: ObjectStore, *, passphrase: str) -> ReencryptReport:
    """Seal every mirrored object under a fresh content key.

    Objects already sealed under the new key are left alone, so an interrupted
    run is simply re-run. Objects under the previous key stay readable
    throughout — the manifest keeps carrying it, and the new manifest is written
    only at the end — which is what makes an interruption safe rather than
    merely survivable.
    """
    listing = store.list_objects("")
    targets = [obj for obj in listing if obj.key != MANIFEST_KEY]
    if manifest_in_listing(listing):
        manifest, cipher = with_new_generation(load_manifest(store), passphrase)
    elif holds_mirrored_objects(listing):
        manifest, cipher = new_manifest(passphrase)
    else:
        raise RemoteSyncEncryptionError(
            "this store holds nothing to re-encrypt. A plain `opensre remote-sync sync` "
            "will seal whatever you upload next."
        )

    resealed: list[str] = []
    adopted = 0
    already_current = 0
    for obj in targets:
        payload = store.get_object(obj.key)
        if envelope.is_sealed(payload):
            if envelope.parse_header(payload).key_id == cipher.active_key_id:
                already_current += 1
                continue
            plaintext = cipher.unseal(obj.key, payload)
        else:
            plaintext = payload
            adopted += 1
        store.put_object(obj.key, cipher.seal(obj.key, plaintext))
        resealed.append(obj.key)

    # Written last, on purpose: until it lands the old manifest still names the
    # previous key as active, and every object opens under one or other
    # generation, so a run interrupted anywhere leaves a readable store.
    save_manifest(store, manifest)
    return ReencryptReport(resealed=resealed, adopted=adopted, already_current=already_current)


def cipher_for(store: ObjectStore, passphrase: str) -> ManifestCipher:
    """The store's cipher, for callers that already know it is encrypted."""
    return open_manifest(load_manifest(store), passphrase)


__all__ = ["ReencryptReport", "cipher_for", "reencrypt", "rotate_passphrase"]
