"""Content encoding boundary used by the remote-sync engine."""

from __future__ import annotations

from typing import Protocol


class ContentCodec(Protocol):
    """Transforms bytes at the local/object-store boundary."""

    def encode(self, key: str, data: bytes) -> bytes:
        """Return the bytes stored remotely for local ``data``."""

    def decode(self, key: str, data: bytes) -> bytes:
        """Return local bytes recovered from remote ``data``."""


class PlaintextContentCodec:
    """Identity codec used by existing, unencrypted configurations."""

    def encode(self, _key: str, data: bytes) -> bytes:
        """Return ``data`` unchanged."""
        return data

    def decode(self, _key: str, data: bytes) -> bytes:
        """Return ``data`` unchanged."""
        return data


PLAINTEXT_CONTENT_CODEC = PlaintextContentCodec()

__all__ = [
    "ContentCodec",
    "PLAINTEXT_CONTENT_CODEC",
    "PlaintextContentCodec",
]
