"""The store's own record of how it is encrypted.

One small JSON object beside the mirrored roots. It holds the KDF salt and cost,
and the root secrets wrapped under the passphrase-derived KEK — never a key in
the clear, so it is no more sensitive than the ciphertext it sits next to. A
second machine needs only the passphrase: everything else it reads from here,
which is why the salt is not something the user has to carry.

``wrapped_keys`` is a map rather than a single value so a re-encrypt can be
interrupted. Objects name the generation that sealed them, and every generation
still listed here can be opened.

The manifest key has no root prefix, so :func:`platform.filestorage.engine.pull`
already declines to map it to a local path — it is fetched and written here, and
never mirrors onto a laptop.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from platform.filestorage.encryption.cipher import ManifestCipher
from platform.filestorage.encryption.keys import (
    RootKey,
    ScryptParams,
    derive_kek,
    derive_root_key,
    generate_root_secret,
    generate_salt,
    unwrap_root_secret,
    wrap_root_secret,
)
from platform.filestorage.errors import RemoteSyncEncryptionError
from platform.filestorage.ports import ObjectStore, RemoteObject

#: Object key the manifest lives under, relative to the configured prefix.
MANIFEST_KEY = ".opensre-sync-manifest.json"

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class EncryptionManifest:
    """How one prefix is keyed."""

    salt: bytes
    params: ScryptParams
    active_key_id: str
    #: hex key id -> root secret wrapped under the KEK.
    wrapped_keys: dict[str, bytes] = field(default_factory=dict)
    created_at: str = ""
    rotated_at: str = ""

    def to_bytes(self) -> bytes:
        """Serialize for upload."""
        return json.dumps(
            {
                "version": MANIFEST_VERSION,
                "kdf": {
                    "name": "scrypt",
                    "n": self.params.n,
                    "r": self.params.r,
                    "p": self.params.p,
                    "salt": base64.b64encode(self.salt).decode(),
                },
                "active_key_id": self.active_key_id,
                "wrapped_keys": {
                    key_id: base64.b64encode(wrapped).decode()
                    for key_id, wrapped in sorted(self.wrapped_keys.items())
                },
                "created_at": self.created_at,
                "rotated_at": self.rotated_at,
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8")


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def parse_manifest(data: bytes) -> EncryptionManifest:
    """Read a manifest, or raise when it is not one this version understands."""
    try:
        raw = json.loads(data)
        version = int(raw["version"])
        if version > MANIFEST_VERSION:
            raise RemoteSyncEncryptionError(
                f"this store's encryption manifest is version {version}; upgrade opensre to use it"
            )
        kdf = raw["kdf"]
        if kdf.get("name") != "scrypt":
            raise RemoteSyncEncryptionError(
                f"unsupported key derivation {kdf.get('name')!r} in this store's manifest"
            )
        return EncryptionManifest(
            salt=base64.b64decode(kdf["salt"]),
            params=ScryptParams(n=int(kdf["n"]), r=int(kdf["r"]), p=int(kdf["p"])),
            active_key_id=str(raw["active_key_id"]),
            wrapped_keys={
                str(key_id): base64.b64decode(value)
                for key_id, value in raw["wrapped_keys"].items()
            },
            created_at=str(raw.get("created_at", "")),
            rotated_at=str(raw.get("rotated_at", "")),
        )
    except RemoteSyncEncryptionError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise RemoteSyncEncryptionError(
            "this store's encryption manifest is damaged and cannot be read"
        ) from exc


def manifest_in_listing(listing: list[RemoteObject]) -> bool:
    """Whether a listing shows a manifest, so absence is never guessed from a 404.

    Reading presence off the listing keeps "no manifest here" distinct from
    "the store could not be reached" — conflating them would create a fresh
    manifest for a store that already had one.
    """
    return any(obj.key == MANIFEST_KEY for obj in listing)


def load_manifest(store: ObjectStore) -> EncryptionManifest:
    """Fetch and parse the manifest. Call only when a listing showed it."""
    return parse_manifest(store.get_object(MANIFEST_KEY))


def save_manifest(store: ObjectStore, manifest: EncryptionManifest) -> None:
    """Upload the manifest."""
    store.put_object(MANIFEST_KEY, manifest.to_bytes())


def new_manifest(passphrase: str) -> tuple[EncryptionManifest, ManifestCipher]:
    """A manifest and cipher for a store that has never been encrypted."""
    salt = generate_salt()
    params = ScryptParams()
    kek = derive_kek(passphrase, salt, params)
    root_secret = generate_root_secret()
    root = derive_root_key(root_secret)
    key_id = root.key_id.hex()
    created = _now()
    manifest = EncryptionManifest(
        salt=salt,
        params=params,
        active_key_id=key_id,
        wrapped_keys={key_id: wrap_root_secret(kek, root_secret)},
        created_at=created,
        rotated_at=created,
    )
    return manifest, ManifestCipher(root)


def open_manifest(manifest: EncryptionManifest, passphrase: str) -> ManifestCipher:
    """Unwrap every key the manifest carries and build a cipher from them.

    Raises :class:`~platform.filestorage.errors.WrongPassphraseError` when the
    passphrase does not open the active key. A retired key that fails to unwrap
    is skipped rather than fatal: it can only make older objects unreadable, and
    failing the whole run would strand a store whose current generation is fine.
    """
    kek = derive_kek(passphrase, manifest.salt, manifest.params)
    wrapped_active = manifest.wrapped_keys.get(manifest.active_key_id)
    if wrapped_active is None:
        raise RemoteSyncEncryptionError(
            "this store's manifest names an active key it does not carry"
        )
    active = derive_root_key(unwrap_root_secret(kek, wrapped_active))
    retired: list[RootKey] = []
    for key_id, wrapped in manifest.wrapped_keys.items():
        if key_id == manifest.active_key_id:
            continue
        try:
            retired.append(derive_root_key(unwrap_root_secret(kek, wrapped)))
        except RemoteSyncEncryptionError:
            continue
    return ManifestCipher(active, retired)


def rewrapped(
    manifest: EncryptionManifest, old_passphrase: str, new_passphrase: str
) -> EncryptionManifest:
    """The same keys, wrapped under a new passphrase.

    No object is touched: the content keys are unchanged, only the wrapping is.
    A key the old passphrase cannot open is dropped rather than carried
    forward — it could not be re-wrapped, and keeping an entry nothing can
    unwrap would only make a later failure harder to read.
    """
    old_kek = derive_kek(old_passphrase, manifest.salt, manifest.params)
    salt = generate_salt()
    params = ScryptParams()
    new_kek = derive_kek(new_passphrase, salt, params)
    rewrapped_keys: dict[str, bytes] = {}
    for key_id, wrapped in manifest.wrapped_keys.items():
        try:
            secret = unwrap_root_secret(old_kek, wrapped)
        except RemoteSyncEncryptionError:
            if key_id == manifest.active_key_id:
                raise
            continue
        rewrapped_keys[key_id] = wrap_root_secret(new_kek, secret)
    return EncryptionManifest(
        salt=salt,
        params=params,
        active_key_id=manifest.active_key_id,
        wrapped_keys=rewrapped_keys,
        created_at=manifest.created_at,
        rotated_at=_now(),
    )


def with_new_generation(
    manifest: EncryptionManifest, passphrase: str
) -> tuple[EncryptionManifest, ManifestCipher]:
    """Add a fresh content key and make it active, keeping the old ones readable."""
    kek = derive_kek(passphrase, manifest.salt, manifest.params)
    root_secret = generate_root_secret()
    root = derive_root_key(root_secret)
    key_id = root.key_id.hex()
    wrapped_keys = dict(manifest.wrapped_keys)
    wrapped_keys[key_id] = wrap_root_secret(kek, root_secret)
    updated = EncryptionManifest(
        salt=manifest.salt,
        params=manifest.params,
        active_key_id=key_id,
        wrapped_keys=wrapped_keys,
        created_at=manifest.created_at,
        rotated_at=_now(),
    )
    return updated, open_manifest(updated, passphrase)


__all__ = [
    "MANIFEST_KEY",
    "MANIFEST_VERSION",
    "EncryptionManifest",
    "load_manifest",
    "manifest_in_listing",
    "new_manifest",
    "open_manifest",
    "parse_manifest",
    "rewrapped",
    "save_manifest",
    "with_new_generation",
]
