"""Filesystem path and write primitives for durable work items."""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

WORK_ITEMS_DIR_MODE = 0o700
WORK_ITEMS_FILE_MODE = 0o600
WORK_ITEMS_FILENAME = "work_items.json"


def work_items_dir() -> Path:
    from config.constants import get_work_items_dir

    return get_work_items_dir()


def work_items_path() -> Path:
    return work_items_dir() / WORK_ITEMS_FILENAME


def ensure_work_items_dir() -> Path:
    directory = work_items_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=WORK_ITEMS_DIR_MODE)
    with contextlib.suppress(OSError):
        directory.chmod(WORK_ITEMS_DIR_MODE)
    return directory


def write_text_atomically(path: Path, text: str) -> None:
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
                tmp_path.chmod(WORK_ITEMS_FILE_MODE)
            tmp_path.replace(path)
    except OSError:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


__all__ = [
    "WORK_ITEMS_DIR_MODE",
    "WORK_ITEMS_FILE_MODE",
    "WORK_ITEMS_FILENAME",
    "ensure_work_items_dir",
    "work_items_dir",
    "work_items_path",
    "write_text_atomically",
]
