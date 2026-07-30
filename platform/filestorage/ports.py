"""Object-store contract the sync engine talks to.

Narrow on purpose: four operations, no vendor types. Backends (S3 today;
others register under :mod:`platform.filestorage.providers`) implement this
protocol. The engine and its tests depend on the protocol, so a sync can be
exercised without a cloud account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RemoteObject:
    """One stored object, as much as the engine needs to compare it.

    ``etag`` is the store's own content tag, returned by a listing so no extra
    request is needed per object. S3 sets it to the MD5 of a single-part upload;
    a multipart upload returns a compound value, which
    :func:`~platform.filestorage.engine.comparable_etag` treats as unusable.
    """

    key: str
    size: int
    last_modified: datetime
    etag: str = ""


class ObjectStore(Protocol):
    """Stores opaque bytes under string keys."""

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        """Every object under ``prefix``; empty when there are none."""

    def get_object(self, key: str) -> bytes:
        """Full contents of one object."""

    def put_object(self, key: str, data: bytes) -> None:
        """Store ``data`` under ``key``."""

    def describe(self) -> str:
        """Short human-readable destination, for logs and CLI output."""


__all__ = ["ObjectStore", "RemoteObject"]
