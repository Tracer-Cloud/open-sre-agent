"""Filesystem path and write primitives for long-term memory."""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

MEMORY_DIR_MODE = 0o700
MEMORY_FILE_MODE = 0o600


def memory_dir() -> Path:
    from config.constants import get_memory_dir

    return get_memory_dir()


def memory_path(slug: str) -> Path:
    return memory_dir() / f"{slug}.md"


def ensure_memory_dir() -> Path:
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
