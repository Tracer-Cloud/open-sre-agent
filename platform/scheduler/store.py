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


def _read_rows(store_path: Path) -> list[dict[str, object]] | None:
    """Rows from disk, or ``None`` when the file exists but cannot be parsed.

    The distinction matters: "no file yet" and "file we could not read" both
    look like zero tasks, but only the first is safe to write over.
    """
    if not store_path.exists():
        return []
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read scheduler store: %s", exc)
        return None
    if not isinstance(data, list):
        logger.warning("Scheduler store is not a JSON list; treating it as unreadable.")
        return None
    return data  # type: ignore[return-value]


def _load_raw(store_path: Path) -> list[dict[str, object]]:
    """Load the raw task list for a read-only caller.

    An unreadable store degrades to "no tasks" here so listing never raises;
    mutating callers use :func:`_load_raw_for_write` instead, which refuses to
    overwrite what it could not read.
    """
    rows = _read_rows(store_path)
    return [] if rows is None else rows


def _load_raw_for_write(store_path: Path) -> list[dict[str, object]]:
    """Load the raw task list for a caller that is about to rewrite the file.

    An unreadable store still holds the operator's schedules. Returning an
    empty list would let the caller write a fresh file straight over them, so
    the damaged file is renamed aside first and stays recoverable.
    """
    rows = _read_rows(store_path)
    if rows is not None:
        return rows
    _quarantine_unreadable(store_path)
    return []


def _quarantine_unreadable(store_path: Path) -> None:
    """Move an unreadable store aside so the next write cannot erase it.

    Raises if the rename fails. Continuing would hand the caller an empty list
    and let it write over the only copy of the schedules — the exact loss this
    preservation step exists to prevent, so it fails closed instead.
    """
    try:
        # mkstemp reserves a unique name atomically. A second-precision
        # timestamp did not: two quarantines inside the same second picked the
        # same path, and the second rename destroyed the first backup — losing
        # the very schedules this step exists to keep.
        handle, backup = tempfile.mkstemp(
            prefix=f"{store_path.name}.corrupt-", dir=store_path.parent
        )
        os.close(handle)
        os.replace(store_path, backup)
    except OSError:
        logger.error(
            "Could not preserve unreadable scheduler store at %s; refusing to "
            "overwrite it. Move or repair the file by hand to continue.",
            store_path,
            exc_info=True,
        )
        raise
    logger.error(
        "Scheduler store at %s was unreadable and has been preserved at %s. "
        "Scheduled tasks it held are not loaded; recover them from that file.",
        store_path,
        backup,
    )


def _save_raw(store_path: Path, data: list[dict[str, object]]) -> None:
    """Persist the task list atomically: temp file, fsync, then replace.

    A plain write truncates the target first, so a process killed mid-write
    leaves a half-written file that parses as nothing. ``os.replace`` is atomic
    on POSIX and Windows, matching how every other local store here writes.
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
        raw = _load_raw_for_write(path)
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
        raw = _load_raw_for_write(path)
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
        raw = _load_raw_for_write(path)
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
