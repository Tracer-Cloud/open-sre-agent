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
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from config.constants.filestorage import (
    REMOTE_SYNC_KEY_CACHE_ENV,
    REMOTE_SYNC_PASSPHRASE_ENV,
)
from config.secrets.backend import KeyringUnavailableError
from config.secrets.store import resolve_secret, save_secret
from platform.filestorage.encryption.envelope import KEY_ID_LEN
from platform.filestorage.errors import (
    MissingPassphraseError,
    WrongPassphraseError,
)

ROOT_SECRET_LEN = 32
KEK_LEN = 32
SALT_LEN = 16
_WRAP_NONCE_LEN = 12

#: Interactive-login cost. ~0.4s on a 2020-era laptop, paid once per machine per
#: salt because the result is cached; see :func:`derive_kek`.
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1

_INFO_CONTENT = b"opensre/remote-sync/content"
_INFO_NONCE = b"opensre/remote-sync/nonce"
_INFO_KEY_ID = b"opensre/remote-sync/key-id"


@dataclass(frozen=True)
class ScryptParams:
    """Cost parameters a store was keyed with.

    Persisted in the manifest so a machine joining later derives the same KEK
    even if the defaults above change in a future release.
    """

    n: int = SCRYPT_N
    r: int = SCRYPT_R
    p: int = SCRYPT_P


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

    The cache is keyed by salt and cost parameters, so a rotated store or a
    changed cost silently misses rather than returning a stale key.
    """
    cached = _cached_kek(salt, params)
    if cached is not None:
        return cached
    kek = Scrypt(salt=salt, length=KEK_LEN, n=params.n, r=params.r, p=params.p).derive(
        passphrase.encode("utf-8")
    )
    _cache_kek(salt, params, kek)
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
            "Check the passphrase, or point at the prefix it belongs to."
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
    """Persist the passphrase through the usual secret tiers."""
    save_secret(REMOTE_SYNC_PASSPHRASE_ENV, passphrase)


def forget_cached_kek() -> None:
    """Drop the cached derivation, so the next call re-derives from scratch."""
    _write_cache("")


def _cache_fingerprint(salt: bytes, params: ScryptParams) -> str:
    return f"{base64.b64encode(salt).decode()}:{params.n}:{params.r}:{params.p}"


def _cached_kek(salt: bytes, params: ScryptParams) -> bytes | None:
    raw = resolve_secret(REMOTE_SYNC_KEY_CACHE_ENV)
    if not raw:
        return None
    try:
        entry = json.loads(raw)
        if entry.get("fingerprint") != _cache_fingerprint(salt, params):
            return None
        return base64.b64decode(entry["kek"])
    except (ValueError, KeyError, TypeError):
        # A damaged cache is a slow sync, not a failed one.
        return None


def _cache_kek(salt: bytes, params: ScryptParams, kek: bytes) -> None:
    _write_cache(
        json.dumps(
            {
                "fingerprint": _cache_fingerprint(salt, params),
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
    "wrap_root_secret",
]
