"""Filesystem CRUD for long-term memories under ``~/.opensre/memory/``.

One markdown file per memory plus a generated ``MEMORY.md`` index. Prompt
injection and ``ensure_memory_store`` create the directory eagerly; writes also
create it lazily. Write failures (disk full, permissions) are reported to
stderr and surfaced as ``None``/``False`` results rather than exceptions,
mirroring :mod:`core.domain.feedback.misses.store`.

Mutating operations serialize through a directory-scoped ``FileLock`` so
concurrent ``memory_remember`` / forget calls cannot silently overwrite each
other or race the index rebuild.
"""

from __future__ import annotations

import contextlib
import sys
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout

from core.domain.memory.files import (
    ensure_memory_dir,
    memory_dir,
    memory_path,
    write_text_atomically,
)
from core.domain.memory.frontmatter import parse_memory_file, serialize_memory
from core.domain.memory.index import (
    DEFAULT_PROMPT_INDEX_CHARS,
    write_index,
)
from core.domain.memory.index import (
    render_prompt_index as render_prompt_index_from_records,
)
from core.domain.memory.models import (
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    TRUNCATION_MARKER,
    MemoryRecord,
    MemoryType,
)
from core.domain.memory.safety import find_memory_safety_issues
from core.domain.memory.slugs import is_valid_slug

_INDEX_FILENAME = "MEMORY.md"
_LOCK_FILENAME = ".memory.lock"
_LOCK_TIMEOUT_SECONDS = 10.0


def ensure_memory_store() -> Path:
    """Create the memory directory and an empty ``MEMORY.md`` when absent.

    Lives here rather than beside the other path helpers because seeding the
    index needs :mod:`core.domain.memory.index`, and the file primitives must
    not depend on anything above them.

    Safe to call on every chat turn / ``/memory`` invocation — mkdir is
    idempotent and the index file is only seeded once.
    """
    directory = ensure_memory_dir()
    index_path = directory / _INDEX_FILENAME
    if not index_path.exists():
        with contextlib.suppress(OSError):
            write_index(directory, [])
    return directory


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _memory_lock() -> FileLock:
    directory = ensure_memory_dir()
    return FileLock(str(directory / _LOCK_FILENAME), timeout=_LOCK_TIMEOUT_SECONDS)


def save_memory(
    *,
    slug: str,
    memory_type: MemoryType,
    description: str,
    body: str,
) -> tuple[MemoryRecord, bool] | None:
    """Create or update a memory; returns ``(record, created)`` or ``None`` on I/O failure.

    Updates preserve ``created_at`` from the existing file. The ``MEMORY.md``
    index is rebuilt after every successful write. Read-modify-write runs under
    the memory-directory lock so parallel writers to the same slug cannot both
    report ``created=True`` or discard each other's content.
    """
    if not is_valid_slug(slug):
        raise ValueError(f"invalid memory slug: {slug!r}")
    clean_description = _single_line(description)[:MAX_DESCRIPTION_CHARS]
    clean_body = body.strip()
    if len(clean_body) > MAX_BODY_CHARS:
        clean_body = clean_body[: MAX_BODY_CHARS - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    issues = find_memory_safety_issues(clean_description, clean_body)
    if issues:
        raise ValueError(
            "memory content rejected by safety checks: " + ",".join(issue.rule for issue in issues)
        )

    try:
        with _memory_lock():
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
            write_text_atomically(memory_path(slug), serialize_memory(record))
            _rebuild_index_best_effort()
            return record, existing is None
    except Timeout:
        print(f"[memory] timed out acquiring lock to save memory {slug!r}", file=sys.stderr)
        return None
    except OSError as exc:
        print(f"[memory] failed to save memory {slug!r}: {exc}", file=sys.stderr)
        return None


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
        with _memory_lock():
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            _rebuild_index_best_effort()
            return True
    except Timeout:
        print(f"[memory] timed out acquiring lock to delete memory {slug!r}", file=sys.stderr)
        return False
    except OSError as exc:
        print(f"[memory] failed to delete memory {slug!r}: {exc}", file=sys.stderr)
        return False


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


def rebuild_index() -> Path:
    """Rewrite MEMORY.md from a fresh directory scan. May raise ``OSError``."""
    return write_index(ensure_memory_dir(), list_memories())


def render_prompt_index(*, max_chars: int = DEFAULT_PROMPT_INDEX_CHARS) -> str:
    """Memory facts for chat/action prompts; ``""`` when nothing is stored.

    Ensures the on-disk store exists so the first chat does not depend on a
    prior write to create ``~/.opensre/memory``.
    """
    ensure_memory_store()
    return render_prompt_index_from_records(list_memories(), max_chars=max_chars)


def _rebuild_index_best_effort() -> None:
    # A failed rebuild leaves MEMORY.md stale until the next successful write.
    # That is tolerable because MEMORY.md is a human-facing convenience only:
    # every agent-facing read (list_memories, render_prompt_index) scans the
    # directory directly, so a stale index never affects recall or extraction.
    try:
        rebuild_index()
    except OSError as exc:
        print(f"[memory] failed to rebuild {_INDEX_FILENAME}: {exc}", file=sys.stderr)


__all__ = [
    "delete_memory",
    "ensure_memory_store",
    "list_memories",
    "load_memory",
    "memory_dir",
    "memory_path",
    "rebuild_index",
    "render_prompt_index",
    "save_memory",
    "search_memories",
]
