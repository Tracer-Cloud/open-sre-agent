"""Object-store contract the sync engine talks to.

Narrow on purpose: four operations, no vendor types. S3 is the only
implementation today; the engine and its tests depend on this instead, so a
sync can be exercised without AWS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# Object metadata key holding the content digest, so an unchanged file is
# recognised without downloading it.
DIGEST_METADATA_KEY = "opensre-sha256"


@dataclass(frozen=True)
class RemoteObject:
    """One stored object, as much as the engine needs to compare it."""

    key: str
    size: int
    last_modified: datetime
    digest: str = ""


class ObjectStore(Protocol):
    """Stores opaque bytes under string keys."""

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        """Every object under ``prefix``; empty when there are none."""

    def get_object(self, key: str) -> bytes:
        """Full contents of one object."""

    def put_object(self, key: str, data: bytes, *, digest: str) -> None:
        """Store ``data`` under ``key``, recording ``digest`` as metadata."""

    def describe(self) -> str:
        """Short human-readable destination, for logs and CLI output."""


__all__ = ["DIGEST_METADATA_KEY", "ObjectStore", "RemoteObject"]
