"""Filesystem path and write primitives for long-term memory.

The lowest layer of the memory package: it knows where files live and how to
write one safely, and imports nothing else from the package. Seeding the index
needs :mod:`core.domain.memory.index`, so it lives in
:mod:`core.domain.memory.store`, which already sits above both.
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

# Unix permission bits, one octal digit per audience: owner, group, everyone
# else. Each digit adds read (4), write (2) and execute (1).
#
# 0o700 on a directory — owner gets 7 (4+2+1: list it, create and delete entries,
# and enter it), group and everyone else get 0. On a directory the execute bit is
# what allows entering at all, so without it the owner could not reach the files
# inside.
MEMORY_DIR_MODE = 0o700

# 0o600 on a file — owner gets 6 (4+2: read and write), group and everyone else
# get 0. No execute bit: these are notes, never run.
#
# Both are owner-only because memory holds whatever the user told the agent to
# remember, which can include names, hosts and incident detail. On a shared
# machine the default would otherwise leave them world-readable.
MEMORY_FILE_MODE = 0o600


def memory_dir() -> Path:
    from config.constants import get_memory_dir

    return get_memory_dir()


def memory_path(slug: str) -> Path:
    return memory_dir() / f"{slug}.md"


def ensure_memory_dir() -> Path:
    """Create ``~/.opensre/memory`` (or ``OPENSRE_MEMORY_DIR``) if missing."""
    directory = memory_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=MEMORY_DIR_MODE)
    with contextlib.suppress(OSError):
        directory.chmod(MEMORY_DIR_MODE)
    return directory


def write_text_atomically(path: Path, text: str) -> None:
    """Write ``text`` via a same-directory temp file, then replace atomically."""
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.chmod(MEMORY_FILE_MODE)
            tmp_path.replace(path)
    except OSError:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


__all__ = [
    "MEMORY_DIR_MODE",
    "MEMORY_FILE_MODE",
    "ensure_memory_dir",
    "memory_dir",
    "memory_path",
    "write_text_atomically",
]
