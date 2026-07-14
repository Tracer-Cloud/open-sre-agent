"""Filesystem CRUD for long-term memories under ``~/.opensre/memory/``.

One markdown file per memory plus a generated ``MEMORY.md`` index. Reads never
create the directory; writes create it lazily. Write failures (disk full,
permissions) are reported to stderr and surfaced as ``None``/``False`` results
rather than exceptions, mirroring :mod:`core.domain.feedback.misses.store`.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from core.domain.memory.frontmatter import parse_memory_file, serialize_memory
from core.domain.memory.models import (
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    TRUNCATION_MARKER,
    MemoryRecord,
    MemoryType,
)
from core.domain.memory.slugs import is_valid_slug

_INDEX_FILENAME = "MEMORY.md"


def memory_dir() -> Path:
    from config.constants import get_memory_dir

    return get_memory_dir()


def memory_path(slug: str) -> Path:
    return memory_dir() / f"{slug}.md"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _single_line(text: str) -> str:
    return " ".join(text.split())


def save_memory(
    *,
    slug: str,
    memory_type: MemoryType,
    description: str,
    body: str,
) -> tuple[MemoryRecord, bool] | None:
    """Create or update a memory; returns ``(record, created)`` or ``None`` on I/O failure.

    Updates preserve ``created_at`` from the existing file. The ``MEMORY.md``
    index is rebuilt after every successful write.
    """
    if not is_valid_slug(slug):
        raise ValueError(f"invalid memory slug: {slug!r}")
    clean_description = _single_line(description)[:MAX_DESCRIPTION_CHARS]
    clean_body = body.strip()
    if len(clean_body) > MAX_BODY_CHARS:
        clean_body = clean_body[: MAX_BODY_CHARS - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER

    existing = load_memory(slug)
    now = _now_iso()
    record = MemoryRecord(
        slug=slug,
        memory_type=memory_type,
        description=clean_description,
        created_at=existing.created_at if existing else now,
        updated_at=now,
        body=clean_body,
    )
    try:
        memory_dir().mkdir(parents=True, exist_ok=True)
        memory_path(slug).write_text(serialize_memory(record), encoding="utf-8")
    except OSError as exc:
        print(f"[memory] failed to save memory {slug!r}: {exc}", file=sys.stderr)
        return None
    _rebuild_index_best_effort()
    return record, existing is None


def load_memory(slug: str) -> MemoryRecord | None:
    if not is_valid_slug(slug):
        return None
    try:
        text = memory_path(slug).read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_memory_file(text)


def list_memories() -> list[MemoryRecord]:
    """All parseable memories, most recently updated first."""
    directory = memory_dir()
    if not directory.is_dir():
        return []
    records: list[MemoryRecord] = []
    for path in directory.glob("*.md"):
        if path.name == _INDEX_FILENAME:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        record = parse_memory_file(text)
        if record is not None:
            records.append(record)
    records.sort(key=lambda r: r.updated_at, reverse=True)
    return records


def delete_memory(slug: str) -> bool:
    if not is_valid_slug(slug):
        return False
    path = memory_path(slug)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        print(f"[memory] failed to delete memory {slug!r}: {exc}", file=sys.stderr)
        return False
    _rebuild_index_best_effort()
    return True


def search_memories(query: str, *, limit: int = 5) -> list[MemoryRecord]:
    """Case-insensitive substring search over slug, description, and body."""
    needle = query.strip().lower()
    if not needle:
        return []
    matches = [
        record
        for record in list_memories()
        if needle in record.slug
        or needle in record.description.lower()
        or needle in record.body.lower()
    ]
    return matches[: max(limit, 0)]


def _rebuild_index_best_effort() -> None:
    from core.domain.memory.index import rebuild_index

    try:
        rebuild_index()
    except OSError as exc:
        print(f"[memory] failed to rebuild {_INDEX_FILENAME}: {exc}", file=sys.stderr)


__all__ = [
    "delete_memory",
    "list_memories",
    "load_memory",
    "memory_dir",
    "memory_path",
    "save_memory",
    "search_memories",
]
