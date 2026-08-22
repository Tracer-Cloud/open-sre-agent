"""Changing the passphrase, and re-keying the objects themselves.

**Rotating the passphrase** re-wraps the content keys: one small object, instant
whatever the store's size, and the old passphrase stops working at once.

**Re-encrypting** mints a new content key and rewrites every object, costing a
full upload. Objects are overwritten in place, so old ciphertext is replaced —
except on a **versioned** bucket, where superseded versions remain readable
under the old key and are the operator's to remove. It is also the migration
path, so it accepts objects that were never sealed.
"""

from __future__ import annotations

from dataclasses import dataclass

from infrastructure.filestorage.contracts import ObjectStore
from infrastructure.filestorage.encryption import envelope
from infrastructure.filestorage.encryption.cipher import ManifestCipher
from infrastructure.filestorage.encryption.keys import forget_cached_kek
from infrastructure.filestorage.encryption.manifest import (
    MANIFEST_KEY,
    load_manifest,
    manifest_in_listing,
    new_manifest,
    open_manifest,
    rewrapped,
    save_manifest,
    with_new_generation,
)
from infrastructure.filestorage.encryption.resolver import holds_mirrored_objects
from infrastructure.filestorage.errors import RemoteSyncEncryptionError


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
            "This store is not encrypted, so there is no passphrase to rotate.\n"
            "\n"
            "  Turn encryption on first:  opensre remote-sync setup"
        )
    updated = rewrapped(load_manifest(store), old_passphrase, new_passphrase)
    save_manifest(store, updated)
    forget_cached_kek()


def reencrypt(store: ObjectStore, *, passphrase: str) -> ReencryptReport:
    """Seal every mirrored object under a fresh content key.

    The manifest is written **before** the first object is resealed, and that
    order is load-bearing. A key exists only in this process until it is
    persisted, so anything sealed under an unpersisted key is lost the moment
    the run dies. Writing first costs nothing — the manifest carries the old
    generation too, so every object opens under one or the other — and makes an
    interrupted run resumable rather than destructive.

    Objects already sealed under the new key are skipped, so re-running after a
    failure finishes the job.
    """
    listing = store.list_objects("")
    targets = [obj for obj in listing if obj.key != MANIFEST_KEY]
    if manifest_in_listing(listing):
        manifest, cipher = with_new_generation(load_manifest(store), passphrase)
    elif holds_mirrored_objects(listing):
        manifest, cipher = new_manifest(passphrase)
    else:
        raise RemoteSyncEncryptionError(
            "This store holds nothing to re-encrypt.\n"
            "A plain `opensre remote-sync sync` will seal whatever you upload next."
        )

    # Before the loop, never after: a key that is not yet in the store(manifest josn) dies with
    # the process, taking every object already sealed under it.
    save_manifest(store, manifest)

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

    return ReencryptReport(resealed=resealed, adopted=adopted, already_current=already_current)


def cipher_for(store: ObjectStore, passphrase: str) -> ManifestCipher:
    """The store's cipher, for callers that already know it is encrypted."""
    return open_manifest(load_manifest(store), passphrase)


__all__ = ["ReencryptReport", "cipher_for", "reencrypt", "rotate_passphrase"]
