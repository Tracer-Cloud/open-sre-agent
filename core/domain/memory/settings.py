"""Environment gates for the long-term memory feature."""

from __future__ import annotations

import os

_TRUTHY = frozenset({"1", "true", "yes"})


def _flag_set(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def memory_enabled() -> bool:
    """Master switch: ``OPENSRE_MEMORY_DISABLED=1`` turns the whole feature off."""
    return not _flag_set("OPENSRE_MEMORY_DISABLED")


def auto_extract_enabled() -> bool:
    """Session-end LLM extraction; also off whenever memory itself is disabled."""
    return memory_enabled() and not _flag_set("OPENSRE_MEMORY_AUTOEXTRACT_DISABLED")


__all__ = ["auto_extract_enabled", "memory_enabled"]
