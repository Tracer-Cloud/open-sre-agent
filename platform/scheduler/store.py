"""JSON-backed task definition CRUD with file locking."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from filelock import FileLock

from config.constants import OPENSRE_HOME_DIR
from platform.scheduler.claim_store import _DB_FILENAME, delete_runs
from platform.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)

_STORE_FILENAME = "scheduler_tasks.json"


def _default_store_path() -> Path:
    return OPENSRE_HOME_DIR / _STORE_FILENAME


def _lock_path(store_path: Path) -> Path:
    return store_path.with_suffix(".lock")


def _load_raw(store_path: Path) -> list[dict[str, object]]:
    """Load raw task list from disk."""
    if not store_path.exists():
        return []
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read scheduler store: %s", exc)
        return []
    if not isinstance(data, list):
        return []
    return data  # type: ignore[return-value]


def _save_raw(store_path: Path, data: list[dict[str, object]]) -> None:
    """Persist the task list by atomic rename.

    ``Path.write_text`` truncates before rewriting, so a crash inside that
    window leaves a half-written file that no longer parses. Every other local
    store in the repo writes through a temp file in the destination directory,
    fsyncs, then ``os.replace``; see ``integrations/store.py::_atomic_write``
    and the convention stated in ``platform/filestorage/__init__.py``.
    """
    store_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, default=str) + "\n"
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=store_path.parent, prefix=store_path.name + ".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        # os.replace is atomic on POSIX and Windows.
        os.replace(tmp_path, store_path)
    except Exception:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
        raise


def list_tasks(store_path: Path | None = None) -> list[ScheduledTask]:
    """Return all persisted scheduled tasks."""
    path = store_path or _default_store_path()
    lock = FileLock(_lock_path(path))
    with lock:
        raw = _load_raw(path)
    tasks: list[ScheduledTask] = []
    for entry in raw:
        try:
            tasks.append(ScheduledTask.model_validate(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping invalid task entry: %s", exc)
    return tasks


def get_task(task_id: str, store_path: Path | None = None) -> ScheduledTask | None:
    """Return a single task by ID, or None if not found."""
    for task in list_tasks(store_path):
        if task.id == task_id:
            return task
    return None


def _schedule_identity(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    """What makes two rows the same schedule.

    Full configuration, not just the slot: two rows differing in destination or
    params are separate reports, and merging them would drop one the user asked
    for. Identity deliberately excludes ``id``, ``name`` and the run bookkeeping
    (``created_at``, ``last_run``, ``next_run``), which differ between two
    confirmations of the same schedule.
    """
    return (
        entry.get("kind"),
        entry.get("cron"),
        entry.get("timezone"),
        entry.get("provider"),
        entry.get("chat_id"),
        entry.get("window_hours"),
        tuple(sorted((entry.get("params") or {}).items())),
    )


def add_task(task: ScheduledTask, store_path: Path | None = None) -> ScheduledTask:
    """Persist a scheduled task, or return the identical one already stored.

    Confirming the same schedule twice is one schedule. Without this, every
    confirmation appended a row — a real install reached 37 byte-identical
    ``daily_summary`` entries, none of which could deliver.
    """
    path = store_path or _default_store_path()
    lock = FileLock(_lock_path(path))
    with lock:
        raw = _load_raw(path)
        wanted = _schedule_identity(task.model_dump(mode="json"))
        existing = next(
            (entry for entry in raw if _schedule_identity(entry) == wanted),
            None,
        )
        if existing is not None:
            return ScheduledTask.model_validate(existing)
        raw.append(task.model_dump(mode="json"))
        _save_raw(path, raw)
    return task


def remove_task(task_id: str, store_path: Path | None = None) -> bool:
    """Remove a task by ID and cascade-delete its run records.

    Returns True if the task was found and removed from the JSON store.
    Cascade deletion of ``TaskRun`` records in the SQLite claim store is
    best-effort — a warning is logged on failure but the return value
    reflects only the JSON-store result.
    """
    path = store_path or _default_store_path()
    lock = FileLock(_lock_path(path))
    with lock:
        raw = _load_raw(path)
        original_len = len(raw)
        raw = [entry for entry in raw if entry.get("id") != task_id]
        if len(raw) == original_len:
            return False
        _save_raw(path, raw)

    # Cascade: remove orphaned TaskRun records from the SQLite claim store.
    # Derive the DB path from the same directory as the JSON store.
    db_path = path.with_name(_DB_FILENAME)
    try:
        deleted = delete_runs(task_id, db_path)
        if deleted:
            logger.info("Cascade-deleted %d run(s) for removed task %s", deleted, task_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to cascade-delete runs for task %s (DB: %s); orphaned runs may remain",
            task_id,
            db_path,
            exc_info=True,
        )

    return True


def update_task(task: ScheduledTask, store_path: Path | None = None) -> bool:
    """Update an existing task in the store. Returns True if found and updated."""
    path = store_path or _default_store_path()
    lock = FileLock(_lock_path(path))
    with lock:
        raw = _load_raw(path)
        for i, entry in enumerate(raw):
            if entry.get("id") == task.id:
                raw[i] = task.model_dump(mode="json")
                _save_raw(path, raw)
                return True
    return False


__all__ = [
    "add_task",
    "get_task",
    "list_tasks",
    "remove_task",
    "update_task",
]
