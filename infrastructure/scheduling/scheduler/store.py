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
from infrastructure.scheduling.scheduler import reload_signal
from infrastructure.scheduling.scheduler.claim_store import _DB_FILENAME, delete_runs
from infrastructure.scheduling.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)

_STORE_FILENAME = "scheduler_tasks.json"


def _default_store_path() -> Path:
    return OPENSRE_HOME_DIR / _STORE_FILENAME


def _lock_path(store_path: Path) -> Path:
    return store_path.with_suffix(".lock")


def _fsync_parent_dir(path: Path) -> None:
    """Sync the directory entry after a replacement on Unix."""
    if os.name == "nt":
        return
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_rows(store_path: Path) -> list[dict[str, object]] | None:
    """Read task rows, or ``None`` when an existing store is not trustworthy."""
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        logger.warning("Failed to read scheduler store: %s", exc)
        return None
    if not isinstance(data, list):
        logger.warning("Scheduler store is not a JSON list; treating it as unreadable")
        return None
    return data  # type: ignore[return-value]


def _load_raw(store_path: Path) -> list[dict[str, object]]:
    """Load task rows for a read-only operation."""
    rows = _read_rows(store_path)
    return [] if rows is None else rows


def _quarantine_unreadable(store_path: Path) -> None:
    """Move an unreadable store aside before a mutation can replace it."""
    descriptor: int | None = None
    backup_path: Path | None = None
    replaced = False
    try:
        descriptor, backup_name = tempfile.mkstemp(
            dir=store_path.parent,
            prefix=f"{store_path.name}.corrupt-",
        )
        backup_path = Path(backup_name)
        os.close(descriptor)
        descriptor = None
        os.replace(store_path, backup_path)
        replaced = True
        _fsync_parent_dir(store_path)
    except OSError:
        logger.error(
            "Could not preserve unreadable scheduler store at %s; refusing to overwrite it",
            store_path,
            exc_info=True,
        )
        raise
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if not replaced and backup_path is not None:
            with contextlib.suppress(OSError):
                backup_path.unlink()
    logger.error(
        "Scheduler store at %s was unreadable and has been preserved at %s",
        store_path,
        backup_path,
    )


def _load_raw_for_write(store_path: Path) -> list[dict[str, object]]:
    """Load task rows for a mutation, preserving unreadable existing data."""
    rows = _read_rows(store_path)
    if rows is None:
        _quarantine_unreadable(store_path)
        return []
    return rows


def _save_raw(store_path: Path, data: list[dict[str, object]]) -> None:
    """Persist task rows with a same-directory atomic replacement."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, default=str) + "\n"
    descriptor: int | None = None
    temp_path: Path | None = None
    replaced = False
    try:
        descriptor, temp_name = tempfile.mkstemp(
            dir=store_path.parent,
            prefix=f"{store_path.name}.tmp",
        )
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, store_path)
        replaced = True
        _fsync_parent_dir(store_path)
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if not replaced and temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()


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
    # A new task changed the schedule: wake any running scheduler to resync.
    reload_signal.request_scheduler_reload()
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

    # The schedule changed: wake any running scheduler so it stops firing this.
    reload_signal.request_scheduler_reload()

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
