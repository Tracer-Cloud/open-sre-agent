"""Contract the sync engine uses to seal and open object payloads.

Narrow on purpose, and deliberately free of key material: the engine holds a
:class:`Cipher` and never learns how keys are derived, wrapped, or rotated. That
keeps ``cryptography`` out of :mod:`platform.filestorage.engine` and lets the
engine's tests exercise encrypted transfers with a trivial stand-in.
"""

from __future__ import annotations

from typing import Protocol


class Cipher(Protocol):
    """Seals object payloads, binding each one to the key it is stored under."""

    def seal(self, object_key: str, plaintext: bytes) -> bytes:
        """Sealed payload for ``plaintext`` as stored at ``object_key``.

        **Must be deterministic.** The engine compares the content tag of a
        freshly sealed local file against the store's ETag to decide whether
        anything changed; a nondeterministic seal would make every file look
        modified on every sync and strand the comparison on mtimes alone.
        """

    def unseal(self, object_key: str, payload: bytes) -> bytes:
        """Plaintext inside ``payload``, which must have been sealed at ``object_key``.

        Raises
        :class:`~platform.filestorage.errors.UndecryptableObjectError` when the
        payload is not a valid envelope, was sealed under a key this cipher
        does not hold, or fails authentication — including when it was stored
        under a different object key.
        """


__all__ = ["Cipher"]
