"""Authenticated client-side encoding for remote-sync objects."""

from __future__ import annotations

import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from config.constants.filestorage import REMOTE_SYNC_PASSPHRASE_ENV
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.content_codec import (
    PLAINTEXT_CONTENT_CODEC,
    ContentCodec,
)
from platform.filestorage.errors import (
    RemoteSyncConfigError,
    RemoteSyncEncryptionError,
)

_ENVELOPE_MAGIC = b"opensre-remote-sync\x00"
_ENVELOPE_VERSION = b"\x01"
_ENVELOPE_HEADER = _ENVELOPE_MAGIC + _ENVELOPE_VERSION
_KDF_CONTEXT = b"opensre.remote-sync.encryption.v1\x00"
_KDF_KEY_BYTES = 64
_KDF_N = 2**14
_KDF_R = 8
_KDF_P = 1
_KDF_SALT_BYTES = 16
_KEY_LENGTH_BYTES = 4


class EncryptedContentCodec:
    """Deterministic authenticated encryption bound to each object key.

    AES-SIV is deliberately deterministic here: the sync engine compares the
    provider's content tag with the bytes it would upload. Randomized
    ciphertext would make an unchanged file appear different on every run.
    Object names, sizes, update timing, and equality at the same key remain
    visible to the store; content and authenticity do not.
    """

    def __init__(self, key: bytes) -> None:
        self._cipher = AESSIV(key)

    def encode(self, key: str, data: bytes) -> bytes:
        """Encrypt and authenticate ``data`` for exactly ``key``."""
        key_bytes = key.encode("utf-8")
        payload = len(key_bytes).to_bytes(_KEY_LENGTH_BYTES, "big") + key_bytes + data
        ciphertext = self._cipher.encrypt(payload, None)
        return _ENVELOPE_HEADER + ciphertext

    def decode(self, key: str, data: bytes) -> bytes:
        """Decrypt ``data``, rejecting wrong keys, tampering, and plaintext."""
        if not data.startswith(_ENVELOPE_HEADER):
            raise RemoteSyncEncryptionError(
                "Remote content is not encrypted with the configured format. "
                "Restore the previous encryption setting or use a new prefix."
            )
        ciphertext = data[len(_ENVELOPE_HEADER) :]
        try:
            payload = self._cipher.decrypt(ciphertext, None)
        except InvalidTag as exc:
            raise RemoteSyncEncryptionError(
                "Remote content could not be decrypted. Check the remote-sync "
                "passphrase and object prefix."
            ) from exc
        if len(payload) < _KEY_LENGTH_BYTES:
            raise RemoteSyncEncryptionError("Remote encrypted content has an invalid envelope.")
        key_length = int.from_bytes(payload[:_KEY_LENGTH_BYTES], "big")
        key_end = _KEY_LENGTH_BYTES + key_length
        if key_end > len(payload):
            raise RemoteSyncEncryptionError("Remote encrypted content has an invalid envelope.")
        stored_key = payload[_KEY_LENGTH_BYTES:key_end]
        if stored_key != key.encode("utf-8"):
            raise RemoteSyncEncryptionError(
                "Remote encrypted content belongs to a different object path."
            )
        return payload[key_end:]


def _store_salt(config: RemoteSyncConfig) -> bytes:
    """Stable, store-specific Scrypt salt shared by matching configurations."""
    identity = "\x00".join((config.provider, config.bucket, config.prefix)).encode("utf-8")
    return hashlib.sha256(_KDF_CONTEXT + identity).digest()[:_KDF_SALT_BYTES]


def _derive_key(config: RemoteSyncConfig, passphrase: str) -> bytes:
    """Derive one in-memory AES-SIV key for this sync invocation."""
    return Scrypt(
        salt=_store_salt(config),
        length=_KDF_KEY_BYTES,
        n=_KDF_N,
        r=_KDF_R,
        p=_KDF_P,
    ).derive(passphrase.encode("utf-8"))


def content_codec_for(config: RemoteSyncConfig) -> ContentCodec:
    """Build the configured codec without retaining the passphrase."""
    if not config.encryption:
        return PLAINTEXT_CONTENT_CODEC
    passphrase = os.getenv(REMOTE_SYNC_PASSPHRASE_ENV, "")
    if not passphrase:
        raise RemoteSyncConfigError(
            f"Client-side encryption is enabled but {REMOTE_SYNC_PASSPHRASE_ENV} is not set."
        )
    return EncryptedContentCodec(_derive_key(config, passphrase))


__all__ = [
    "EncryptedContentCodec",
    "content_codec_for",
]
