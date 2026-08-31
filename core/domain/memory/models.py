"""Typed contracts for the long-term memory store."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MemoryType(StrEnum):
    """Closed vocabulary for a persisted memory's frontmatter ``type``."""

    USER = "user"
    INFRASTRUCTURE = "infrastructure"
    REPOSITORY = "repository"
    PREFERENCE = "preference"
    INVESTIGATION_LEARNING = "investigation_learning"


MEMORY_TYPES: tuple[str, ...] = tuple(t.value for t in MemoryType)

MAX_DESCRIPTION_CHARS = 200
MAX_BODY_CHARS = 10_000
MAX_SLUG_CHARS = 64

TRUNCATION_MARKER = "\n...[truncated]"


@dataclass(frozen=True)
class MemoryRecord:
    """One persisted long-term memory (a single markdown file on disk)."""

    slug: str
    memory_type: MemoryType
    description: str
    created_at: str
    updated_at: str
    body: str

    def __post_init__(self) -> None:
        # Coerce plain strings (from disk / tool args) into real enum members so
        # ``memory_type`` is always a ``MemoryType`` regardless of entry point.
        object.__setattr__(self, "memory_type", MemoryType(self.memory_type))


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_SLUG_CHARS",
    "MEMORY_TYPES",
    "TRUNCATION_MARKER",
    "MemoryRecord",
    "MemoryType",
]
