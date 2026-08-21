"""Contract the sync engine uses to seal and open object payloads.

Free of key material on purpose: the engine never learns how keys are derived or
rotated, which keeps ``cryptography`` out of its import graph and lets its tests
use a trivial stand-in.
"""

from __future__ import annotations

from typing import Protocol


class Cipher(Protocol):
    """Seals object payloads, binding each one to the key it is stored under."""

    def seal(self, object_key: str, plaintext: bytes) -> bytes:
        """Sealed payload for ``plaintext`` as stored at ``object_key``.

        **Must be deterministic** — the engine compares a freshly sealed local
        file against the store's ETag to detect change.
        """

    def unseal(self, object_key: str, payload: bytes) -> bytes:
        """Plaintext inside ``payload``, which must have been sealed at ``object_key``.

        Raises :class:`~infrastructure.filestorage.errors.UndecryptableObjectError`
        when the payload is malformed, was sealed under a key this cipher does
        not hold, or fails authentication.
        """


__all__ = ["Cipher"]
