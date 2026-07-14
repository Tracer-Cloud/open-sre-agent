"""Typed contracts for the long-term memory store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

MemoryType = Literal["user", "infrastructure", "preference", "investigation_learning"]

MEMORY_TYPES: tuple[str, ...] = get_args(MemoryType)

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


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_SLUG_CHARS",
    "MEMORY_TYPES",
    "TRUNCATION_MARKER",
    "MemoryRecord",
    "MemoryType",
]
