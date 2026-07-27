"""Parse and serialize the memory file format (frontmatter + markdown body).

The frontmatter is a deliberately tiny ``key: value`` block between ``---``
fences — not YAML — so the store needs no third-party parser and malformed
files degrade to ``None`` instead of raising.
"""

from __future__ import annotations

from core.domain.memory.models import MEMORY_TYPES, MemoryRecord, MemoryType
from core.domain.memory.slugs import is_valid_slug

_FENCE = "---"
_REQUIRED_KEYS = ("name", "type", "description", "created", "updated")


def parse_memory_file(text: str) -> MemoryRecord | None:
    """Parse one memory file; return ``None`` for anything malformed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return None
    try:
        close_idx = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == _FENCE)
    except StopIteration:
        return None

    fields: dict[str, str] = {}
    for line in lines[1:close_idx]:
        key, sep, value = line.partition(":")
        if not sep:
            return None
        fields[key.strip()] = value.strip()

    if any(key not in fields for key in _REQUIRED_KEYS):
        return None
    slug = fields["name"]
    memory_type = fields["type"]
    if not is_valid_slug(slug) or memory_type not in MEMORY_TYPES:
        return None

    body = "\n".join(lines[close_idx + 1 :]).strip("\n")
    typed: MemoryType = memory_type  # type: ignore[assignment]
    return MemoryRecord(
        slug=slug,
        memory_type=typed,
        description=fields["description"],
        created_at=fields["created"],
        updated_at=fields["updated"],
        body=body,
    )


def serialize_memory(record: MemoryRecord) -> str:
    # Frontmatter values are single-line by construction (descriptions are
    # sanitized on save); the body is free-form markdown.
    return (
        f"{_FENCE}\n"
        f"name: {record.slug}\n"
        f"type: {record.memory_type}\n"
        f"description: {record.description}\n"
        f"created: {record.created_at}\n"
        f"updated: {record.updated_at}\n"
        f"{_FENCE}\n"
        f"{record.body}\n"
    )


__all__ = ["parse_memory_file", "serialize_memory"]
