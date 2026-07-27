"""Slug normalization and validation for memory file names."""

from __future__ import annotations

import re

from core.domain.memory.models import MAX_SLUG_CHARS

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")

# MEMORY.md is the generated index; a memory named "memory" would collide with
# it case-insensitively on macOS/Windows filesystems.
_RESERVED_SLUGS = frozenset({"memory"})


def slugify(name: str) -> str:
    """Normalize free-form text into a slug; returns "" when nothing usable remains."""
    collapsed = _NON_SLUG_CHARS_RE.sub("-", name.strip().lower()).strip("-")
    return collapsed[:MAX_SLUG_CHARS].strip("-")


def is_valid_slug(slug: str) -> bool:
    return (
        0 < len(slug) <= MAX_SLUG_CHARS
        and slug not in _RESERVED_SLUGS
        and _SLUG_RE.fullmatch(slug) is not None
    )


__all__ = ["is_valid_slug", "slugify"]
