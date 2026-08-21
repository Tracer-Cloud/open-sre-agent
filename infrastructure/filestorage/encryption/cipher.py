"""A :class:`~infrastructure.filestorage.encryption.ports.Cipher` over a set of root keys.

Seals under one active key and opens under any key the store's manifest still
carries. Holding several is what makes a re-encrypt interruptible: objects
written before the run and objects written during it name different key ids, and
both stay readable until the old generation is deliberately dropped.
"""

from __future__ import annotations

from collections.abc import Iterable

from infrastructure.filestorage.encryption import envelope
from infrastructure.filestorage.encryption.keys import RootKey
from infrastructure.filestorage.errors import UndecryptableObjectError


class ManifestCipher:
    """Seals with ``active``; opens with ``active`` or any key in ``retired``."""

    def __init__(self, active: RootKey, retired: Iterable[RootKey] = ()) -> None:
        self._active = active
        # Keyed lookup, not a scan: unseal runs once per downloaded object.
        self._by_id: dict[bytes, RootKey] = {key.key_id: key for key in (active, *retired)}

    @property
    def active_key_id(self) -> bytes:
        return self._active.key_id

    def seal(self, object_key: str, plaintext: bytes) -> bytes:
        """Envelope ``plaintext`` under the active key. Deterministic."""
        return envelope.seal(
            content_key=self._active.content_key,
            nonce_key=self._active.nonce_key,
            key_id=self._active.key_id,
            object_key=object_key,
            plaintext=plaintext,
        )

    def unseal(self, object_key: str, payload: bytes) -> bytes:
        """Open ``payload``, choosing the key it names."""
        header = envelope.parse_header(payload)
        key = self._by_id.get(header.key_id)
        if key is None:
            raise UndecryptableObjectError(
                f"{object_key} was sealed with a key this store no longer carries.\n"
                "It was most likely written after a re-encrypt on another machine."
            )
        return envelope.unseal(content_key=key.content_key, object_key=object_key, payload=payload)


__all__ = ["ManifestCipher"]
