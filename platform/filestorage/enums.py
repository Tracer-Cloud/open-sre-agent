"""Closed vocabularies for remote sync.

Open/extensible names (community object-store providers) stay plain strings in
the registry. Use these enums only where the set is fixed and shared across
engine + surfaces.
"""

from __future__ import annotations

from enum import StrEnum


class SyncDirection(StrEnum):
    """Which way one sync moves files."""

    BOTH = "both"
    PULL = "pull"
    PUSH = "push"


class SyncRootName(StrEnum):
    """Allowlisted roots that may leave the machine (object-key prefixes)."""

    SESSIONS = "sessions"
    MEMORY = "memory"


class BuiltInProvider(StrEnum):
    """Shipped object-store backends.

    Community providers register under any other string name — they are not
    listed here on purpose, so this enum stays closed.
    """

    AWS = "aws"


class RemoteSyncSubcommand(StrEnum):
    """CLI / slash verbs shared by ``opensre remote-sync`` and ``/remote-sync``."""

    STATUS = "status"
    SYNC = "sync"


__all__ = [
    "BuiltInProvider",
    "RemoteSyncSubcommand",
    "SyncDirection",
    "SyncRootName",
]
