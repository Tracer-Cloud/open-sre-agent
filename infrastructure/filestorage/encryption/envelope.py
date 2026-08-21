"""On-the-wire format for one sealed object.

::

    magic       b"OSRE"     4 bytes
    version     uint8       1
    key_id      16 bytes    which root key sealed this
    nonce       12 bytes    derived, not random - see below
    ciphertext + GCM tag    remainder

The object key is authenticated as associated data but never stored, so an
object copied or renamed inside the store fails to open.

The nonce is ``HMAC(nonce_key, object_key || plaintext)``, not random, because
the engine detects change by comparing the store's ETag against the MD5 of what
it would upload — a random nonce would make every file look modified on every
sync. Safe despite the usual GCM warning: an equal nonce implies equal plaintext
and therefore equal ciphertext, so no key/nonce pair ever covers two different
messages.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from infrastructure.filestorage.errors import UndecryptableObjectError

MAGIC = b"OSRE"
VERSION = 1
KEY_ID_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16

HEADER_LEN = len(MAGIC) + 1 + KEY_ID_LEN + NONCE_LEN
#: Bytes a sealed object adds to its plaintext. Reported to users, who compare
#: local file sizes against what the store shows.
OVERHEAD = HEADER_LEN + TAG_LEN

_KEY_ID_END = len(MAGIC) + 1 + KEY_ID_LEN


@dataclass(frozen=True)
class EnvelopeHeader:
    """The parsed prefix of a sealed object."""

    key_id: bytes
    nonce: bytes


def is_sealed(payload: bytes) -> bool:
    """Whether ``payload`` looks like an envelope this module wrote.

    Used to tell an encrypted store from a plaintext one. A magic-byte check,
    not an integrity check: a positive answer means "treat this as sealed", and
    :func:`unseal` is what decides whether it really is.
    """
    return len(payload) >= HEADER_LEN and payload.startswith(MAGIC)


def parse_header(payload: bytes) -> EnvelopeHeader:
    """Read the envelope prefix, or raise when ``payload`` is not one."""
    if not is_sealed(payload):
        raise UndecryptableObjectError(
            "This object is not an opensre encrypted envelope.\n"
            "It was most likely written before encryption was turned on."
        )
    if payload[len(MAGIC)] != VERSION:
        raise UndecryptableObjectError(
            f"envelope version {payload[len(MAGIC)]} is newer than this opensre understands"
        )
    return EnvelopeHeader(
        key_id=payload[len(MAGIC) + 1 : _KEY_ID_END],
        nonce=payload[_KEY_ID_END:HEADER_LEN],
    )


def derive_nonce(nonce_key: bytes, object_key: str, plaintext: bytes) -> bytes:
    """Nonce for one (object key, plaintext) pair under ``nonce_key``.

    The object key is length-prefixed rather than concatenated so that two
    different splits of key and content cannot produce the same input.
    """
    mac = hmac.HMAC(nonce_key, hashes.SHA256())
    encoded = object_key.encode("utf-8")
    mac.update(len(encoded).to_bytes(4, "big"))
    mac.update(encoded)
    mac.update(plaintext)
    return mac.finalize()[:NONCE_LEN]


def _associated_data(key_id: bytes, object_key: str) -> bytes:
    return b"".join((MAGIC, bytes([VERSION]), key_id, object_key.encode("utf-8")))


def seal(
    *,
    content_key: bytes,
    nonce_key: bytes,
    key_id: bytes,
    object_key: str,
    plaintext: bytes,
) -> bytes:
    """Envelope for ``plaintext`` stored at ``object_key``. Deterministic."""
    nonce = derive_nonce(nonce_key, object_key, plaintext)
    ciphertext = AESGCM(content_key).encrypt(nonce, plaintext, _associated_data(key_id, object_key))
    return b"".join((MAGIC, bytes([VERSION]), key_id, nonce, ciphertext))


def unseal(*, content_key: bytes, object_key: str, payload: bytes) -> bytes:
    """Plaintext inside ``payload``, or raise :class:`UndecryptableObjectError`.

    ``content_key`` must be the one named by the payload's ``key_id``; picking
    it is the caller's job (see :mod:`infrastructure.filestorage.encryption.cipher`).
    """
    header = parse_header(payload)
    try:
        return AESGCM(content_key).decrypt(
            header.nonce,
            payload[HEADER_LEN:],
            _associated_data(header.key_id, object_key),
        )
    except InvalidTag as exc:
        # No detail from the exception: it distinguishes nothing useful here,
        # and this message can reach a chat sink.
        raise UndecryptableObjectError(
            f"{object_key} failed authentication.\n"
            "The key is wrong, or the object was altered or moved inside the store."
        ) from exc


__all__ = [
    "HEADER_LEN",
    "KEY_ID_LEN",
    "MAGIC",
    "NONCE_LEN",
    "OVERHEAD",
    "VERSION",
    "EnvelopeHeader",
    "derive_nonce",
    "is_sealed",
    "parse_header",
    "seal",
    "unseal",
]
