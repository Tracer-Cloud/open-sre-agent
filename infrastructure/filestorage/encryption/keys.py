"""Deriving, wrapping, and caching the keys that seal a store's objects.

Two levels, because the sync engine never deletes: content is sealed under a
random `root key`, which is wrapped by a `KEK` derived from the passphrase.
Changing the passphrase re-wraps ~100 bytes and takes effect at once, whereas
re-keying content would re-upload everything and still revoke nothing.

The passphrase resolves through :mod:`config.secrets.store` — environment, then
local file(~/.opensre/credentials.json). That file is plaintext by design: this key
defends the *remote* store, and anyone who can read it can already read
``~/.opensre/sessions/`` beside it.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from contextlib import suppress
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from config.constants.filestorage import (
    REMOTE_SYNC_KEY_CACHE_ENV,
    REMOTE_SYNC_PASSPHRASE_ENV,
)
from config.secrets.backend import KeyringUnavailableError, SecretTier
from config.secrets.store import lookup, resolve_secret, save_secret
from infrastructure.filestorage.encryption.envelope import KEY_ID_LEN
from infrastructure.filestorage.errors import (
    MissingPassphraseError,
    PassphraseNotResolvableError,
    RemoteSyncEncryptionError,
    WrongPassphraseError,
)

ROOT_SECRET_LEN = 32
KEK_LEN = 32
SALT_LEN = 16
_WRAP_NONCE_LEN = 12

# Scrypt parameters to derive a KEK from a passphrase.
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1

_INFO_CONTENT = b"opensre/remote-sync/content"
_INFO_NONCE = b"opensre/remote-sync/nonce"
_INFO_KEY_ID = b"opensre/remote-sync/key-id"


#: Bounds on what a manifest may ask this machine to compute. The manifest is
#: remote data, so its cost parameters are untrusted input: scrypt allocates
#: ``128 * n * r`` bytes, and an ``n`` of 2**30 asks for a terabyte before any
#: passphrase is checked. The floor matters too — a manifest that lowered the
#: cost to nothing would make the passphrase cheap to attack offline.
MIN_SCRYPT_N = 2**14
MAX_SCRYPT_R = 32
MAX_SCRYPT_P = 16
MAX_SCRYPT_MEMORY_BYTES = 512 * 1024 * 1024
MIN_SALT_LEN = 16
MAX_SALT_LEN = 64


@dataclass(frozen=True)
class ScryptParams:
    """Cost parameters a store was keyed with, validated on construction.

    Persisted in the manifest so a machine joining later derives the same KEK
    even if the defaults above change in a future release. Because that manifest
    is remote data, the range this machine is willing to honour is enforced
    here — where the values become an object — rather than at each call site, so
    no path can reach :class:`Scrypt` with something it never agreed to compute.
    """

    n: int = SCRYPT_N
    r: int = SCRYPT_R
    p: int = SCRYPT_P

    def __post_init__(self) -> None:
        if (
            not isinstance(self.n, int)
            or not isinstance(self.r, int)
            or not isinstance(self.p, int)
        ):
            raise RemoteSyncEncryptionError("this store's manifest has non-integer KDF parameters")
        if self.n < MIN_SCRYPT_N or self.n & (self.n - 1):
            raise RemoteSyncEncryptionError(
                f"this store's manifest asks for scrypt n={self.n}, which is not a power of two "
                f"of at least {MIN_SCRYPT_N}"
            )
        if not 1 <= self.r <= MAX_SCRYPT_R or not 1 <= self.p <= MAX_SCRYPT_P:
            raise RemoteSyncEncryptionError(
                f"this store's manifest asks for scrypt r={self.r}, p={self.p}, outside the "
                f"supported range (r 1-{MAX_SCRYPT_R}, p 1-{MAX_SCRYPT_P})"
            )
        if 128 * self.n * self.r > MAX_SCRYPT_MEMORY_BYTES:
            raise RemoteSyncEncryptionError(
                f"this store's manifest asks for {128 * self.n * self.r // 2**20} MiB of key "
                f"derivation memory, over the {MAX_SCRYPT_MEMORY_BYTES // 2**20} MiB limit"
            )


def validated_salt(salt: bytes) -> bytes:
    """The manifest's salt, or a domain error when it is not a plausible one."""
    if not MIN_SALT_LEN <= len(salt) <= MAX_SALT_LEN:
        raise RemoteSyncEncryptionError(
            f"this store's manifest has a {len(salt)}-byte KDF salt, outside the supported "
            f"{MIN_SALT_LEN}-{MAX_SALT_LEN} bytes"
        )
    return salt


@dataclass(frozen=True)
class RootKey:
    """One generation of content keys, all derived from a single random secret.

    ``key_id`` names the generation and is written into every envelope, so a
    store part-way through a re-encrypt stays fully readable: each object says
    which key opens it.
    """

    key_id: bytes
    content_key: bytes
    nonce_key: bytes


def _hkdf(secret: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(secret)


def derive_root_key(root_secret: bytes) -> RootKey:
    """Expand a root secret into the content key, nonce key, and key id."""
    return RootKey(
        key_id=_hkdf(root_secret, _INFO_KEY_ID, KEY_ID_LEN),
        content_key=_hkdf(root_secret, _INFO_CONTENT, 32),
        nonce_key=_hkdf(root_secret, _INFO_NONCE, 32),
    )


def generate_root_secret() -> bytes:
    """A fresh random root secret."""
    return secrets.token_bytes(ROOT_SECRET_LEN)


def generate_salt() -> bytes:
    """A fresh random KDF salt, stored in the manifest."""
    return secrets.token_bytes(SALT_LEN)


def derive_kek(passphrase: str, salt: bytes, params: ScryptParams) -> bytes:
    """Key-encryption key for ``passphrase``, using the cache when it applies.

    The cache entry is bound to the passphrase as well as the salt and cost, so
    a warm cache can only ever answer for the passphrase that filled it. Keyed
    on the salt alone it would hand back the previous KEK before the supplied
    passphrase was looked at, and a wrong one would open the store — checking
    the unwrap afterwards does not catch that, because the cached KEK unwraps
    the manifest perfectly well.
    """
    cached = _cached_kek(passphrase, salt, params)
    if cached is not None:
        return cached
    kek = Scrypt(salt=salt, length=KEK_LEN, n=params.n, r=params.r, p=params.p).derive(
        passphrase.encode("utf-8")
    )
    _cache_kek(passphrase, salt, params, kek)
    return kek


def wrap_root_secret(kek: bytes, root_secret: bytes) -> bytes:
    """Seal a root secret under the KEK. Random nonce — this is written once."""
    nonce = os.urandom(_WRAP_NONCE_LEN)
    return nonce + AESGCM(kek).encrypt(nonce, root_secret, None)


def unwrap_root_secret(kek: bytes, wrapped: bytes) -> bytes:
    """Open a wrapped root secret, or raise :class:`WrongPassphraseError`.

    A bad passphrase and a tampered manifest are indistinguishable here on
    purpose: both mean this machine cannot speak for this store.
    """
    if len(wrapped) <= _WRAP_NONCE_LEN:
        raise WrongPassphraseError(
            "This store's key material is malformed and cannot be read.\n"
            "The manifest may be truncated or damaged."
        )
    try:
        return AESGCM(kek).decrypt(wrapped[:_WRAP_NONCE_LEN], wrapped[_WRAP_NONCE_LEN:], None)
    except InvalidTag as exc:
        raise WrongPassphraseError(
            "That passphrase does not open this store's key.\n"
            "Check the passphrase, or point at the remote store's prefix it belongs to."
        ) from exc


def resolve_passphrase() -> str:
    """The configured passphrase, or raise :class:`MissingPassphraseError`.

    Never prompts: this runs under the gateway and other headless hosts as well
    as a terminal, and a hidden prompt in a non-interactive process reads as a
    hang. Surfaces that can prompt do so before calling in.
    """
    passphrase = resolve_secret(REMOTE_SYNC_PASSPHRASE_ENV)
    if not passphrase:
        raise MissingPassphraseError(
            "No passphrase is available on this machine.\n"
            f"Run `opensre remote-sync setup`, or export {REMOTE_SYNC_PASSPHRASE_ENV}."
        )
    return passphrase


def save_passphrase(passphrase: str) -> None:
    """Persist the passphrase and confirm this machine reads back exactly it.

    Storing is not the same as resolving, and callers depend on the stronger
    guarantee: a rotation wraps the store under the value passed here, so
    anything that resolves differently afterwards locks this machine out. Two
    tiers can diverge — a write the machine refuses
    (``OPENSRE_DISABLE_KEYRING``), and a stale export that outranks the file
    that was just written — and both raise
    :class:`PassphraseNotResolvableError` rather than surfacing at the next
    command as an unexplained wrong-passphrase error.
    """
    try:
        save_secret(REMOTE_SYNC_PASSPHRASE_ENV, passphrase)
    except KeyringUnavailableError as exc:
        raise PassphraseNotResolvableError(
            f"The passphrase was not stored on this machine, so the next command will\n"
            f"not find it. Export it in every shell that syncs:\n"
            f"\n"
            f"  export {REMOTE_SYNC_PASSPHRASE_ENV}=...\n"
            f"\n"
            f"{exc}"
        ) from exc

    resolved = lookup(REMOTE_SYNC_PASSPHRASE_ENV)
    if resolved.value == passphrase:
        return
    if resolved.tier == SecretTier.ENV:
        raise PassphraseNotResolvableError(
            f"The passphrase was stored, but {REMOTE_SYNC_PASSPHRASE_ENV} is exported in\n"
            f"this environment with a different value and outranks stored credentials,\n"
            f"so the next command would still use the old one. Update or drop it:\n"
            f"\n"
            f"  export {REMOTE_SYNC_PASSPHRASE_ENV}=...   # the new passphrase\n"
            f"  unset {REMOTE_SYNC_PASSPHRASE_ENV}        # or fall back to stored credentials"
        )


def forget_cached_kek() -> None:
    """Drop the cached derivation, so the next call re-derives from scratch."""
    _write_cache("")


def _cache_fingerprint(passphrase: str, salt: bytes, params: ScryptParams) -> str:
    """Identity of one cache entry: passphrase, salt, and cost together.

    The passphrase is bound in as a digest keyed by the salt — never stored —
    so an entry filled by one passphrase cannot be returned for another.
    """
    mac = hmac.HMAC(salt, hashes.SHA256())
    mac.update(passphrase.encode("utf-8"))
    return f"{base64.b64encode(mac.finalize()).decode()}:{params.n}:{params.r}:{params.p}"


def _cached_kek(passphrase: str, salt: bytes, params: ScryptParams) -> bytes | None:
    raw = resolve_secret(REMOTE_SYNC_KEY_CACHE_ENV)
    if not raw:
        return None
    try:
        entry = json.loads(raw)
        if entry.get("fingerprint") != _cache_fingerprint(passphrase, salt, params):
            return None
        return base64.b64decode(entry["kek"])
    except (ValueError, KeyError, TypeError):
        # A damaged cache is a slow sync, not a failed one.
        return None


def _cache_kek(passphrase: str, salt: bytes, params: ScryptParams, kek: bytes) -> None:
    _write_cache(
        json.dumps(
            {
                "fingerprint": _cache_fingerprint(passphrase, salt, params),
                "kek": base64.b64encode(kek).decode(),
            }
        )
    )


def _write_cache(payload: str) -> None:
    """Store the cache entry, or give up quietly.

    Every failure here is survivable by re-deriving, so none of them may fail a
    sync. A machine that has taken itself out of local secret storage
    (``OPENSRE_DISABLE_KEYRING``) is the ordinary case, not an error: it pays
    scrypt once per command and works exactly as well.
    """
    with suppress(KeyringUnavailableError, OSError):
        save_secret(REMOTE_SYNC_KEY_CACHE_ENV, payload)


__all__ = [
    "KEK_LEN",
    "ROOT_SECRET_LEN",
    "SALT_LEN",
    "MAX_SCRYPT_MEMORY_BYTES",
    "MIN_SCRYPT_N",
    "SCRYPT_N",
    "SCRYPT_P",
    "SCRYPT_R",
    "RootKey",
    "ScryptParams",
    "derive_kek",
    "derive_root_key",
    "forget_cached_kek",
    "generate_root_secret",
    "generate_salt",
    "resolve_passphrase",
    "save_passphrase",
    "unwrap_root_secret",
    "validated_salt",
    "wrap_root_secret",
]
