"""SQLite-backed execution claims and run history.

The UNIQUE(task_id, fire_time) constraint prevents double-posting when
multiple scheduler instances race for the same tick.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.constants import OPENSRE_HOME_DIR
from infrastructure.scheduling.scheduler.types import DeliveryOutcome, TaskRun, TaskStatus

logger = logging.getLogger(__name__)

_DB_FILENAME = "scheduler.db"

#: How long to keep retrying a column add while a competing process holds the
#: write lock. Each attempt re-reads the schema first, so a winner's commit
#: ends the loop immediately; this only bounds how long a *stuck* writer is
#: tolerated before the real error surfaces.
_MIGRATION_TIMEOUT_SECONDS = 30.0
_MIGRATION_RETRY_DELAY_SECONDS = 0.1

#: How far back to look for a run with readable per-target history before
#: reporting none. Only a bound on work, not on correctness: exhausting it
#: reads as "history unknown", and a failed-only retry refuses to run rather
#: than widening to every destination.
_TARGETED_RUN_SCAN_LIMIT = 50


def _default_db_path() -> Path:
    return OPENSRE_HOME_DIR / _DB_FILENAME


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode for concurrent readers."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the task_runs table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            fire_time TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            posted_message_id TEXT DEFAULT '',
            error TEXT DEFAULT '',
            provider TEXT DEFAULT '',
            targets TEXT DEFAULT '',
            UNIQUE(task_id, fire_time)
        )
    """)
    _add_missing_columns(conn)
    conn.commit()


def _has_targets_column(conn: sqlite3.Connection) -> bool:
    return "targets" in {str(row[1]) for row in conn.execute("PRAGMA table_info(task_runs)")}


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created.

    Two processes can both see the column missing before either commits its
    ``ALTER TABLE``, so the loser's own write fails — with "duplicate column"
    if the winner already committed, or with a lock-timeout if the winner is
    still in flight and outlasts ``busy_timeout``. Rechecking the schema
    (rather than matching the error text) covers the first case, and retrying
    covers the second: at timeout the winner's column is not visible yet, so
    a single recheck would wrongly conclude the migration failed. Each retry
    re-reads the schema first, so the loser returns the moment the winner's
    commit lands.

    Adding a column to this table is sub-millisecond work, so a writer still
    holding the lock after ``_MIGRATION_TIMEOUT_SECONDS`` is stuck rather than
    slow. Raising then is deliberate: retrying forever would hide a wedged
    database behind a scheduler that never fires.
    """
    deadline = time.monotonic() + _MIGRATION_TIMEOUT_SECONDS
    while True:
        if _has_targets_column(conn):
            return
        try:
            conn.execute("ALTER TABLE task_runs ADD COLUMN targets TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            if _has_targets_column(conn):
                return
            if time.monotonic() >= deadline:
                raise
            time.sleep(_MIGRATION_RETRY_DELAY_SECONDS)
        else:
            return


def _try_create_run(
    task_id: str,
    fire_time: str,
    *,
    status: TaskStatus,
    db_path: Path | None = None,
) -> bool:
    path = db_path or _default_db_path()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO task_runs (task_id, fire_time, started_at, status) "
            "VALUES (?, ?, ?, ?)",
            (task_id, fire_time, now, status.value),
        )
        conn.commit()
        # rowcount == 1 means our INSERT went through; 0 means IGNORE fired
        return cursor.rowcount == 1
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def try_claim(task_id: str, fire_time: str, db_path: Path | None = None) -> bool:
    """Claim a run as running unless another process already created it."""
    return _try_create_run(
        task_id,
        fire_time,
        status=TaskStatus.RUNNING,
        db_path=db_path,
    )


def try_queue_run(task_id: str, fire_time: str, db_path: Path | None = None) -> bool:
    """Record a run as pending unless another process already created it."""
    return _try_create_run(
        task_id,
        fire_time,
        status=TaskStatus.PENDING,
        db_path=db_path,
    )


def try_start_run(task_id: str, fire_time: str, db_path: Path | None = None) -> bool:
    """Start a queued run, or claim it directly if submission was not observed."""
    return start_queued_run(task_id, fire_time, db_path=db_path) or try_claim(
        task_id,
        fire_time,
        db_path=db_path,
    )


def start_queued_run(task_id: str, fire_time: str, db_path: Path | None = None) -> bool:
    """Move a queued claim to running, returning whether the claim exists."""
    path = db_path or _default_db_path()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            "UPDATE task_runs SET status = ?, started_at = ? "
            "WHERE task_id = ? AND fire_time = ? AND status = ?",
            (
                TaskStatus.RUNNING.value,
                datetime.now(UTC).isoformat(),
                task_id,
                fire_time,
                TaskStatus.PENDING.value,
            ),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def complete_run(
    task_id: str,
    fire_time: str,
    *,
    status: TaskStatus,
    posted_message_id: str = "",
    error: str = "",
    provider: str = "",
    targets: Sequence[DeliveryOutcome] = (),
    db_path: Path | None = None,
) -> None:
    """Complete a running claim, recording each destination's outcome.

    ``targets`` is stored in the order it is given, which is the order the run
    planned its destinations in — not the order they finished.
    Pending and terminal records are left unchanged.
    """
    path = db_path or _default_db_path()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE task_runs SET finished_at = ?, status = ?, "
            "posted_message_id = ?, error = ?, provider = ?, targets = ? "
            "WHERE task_id = ? AND fire_time = ? AND status = ?",
            (
                now,
                status.value,
                posted_message_id,
                error,
                provider,
                _encode_targets(targets),
                task_id,
                fire_time,
                TaskStatus.RUNNING.value,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _encode_targets(targets: Sequence[DeliveryOutcome]) -> str:
    """Serialize per-destination outcomes for the ``targets`` column."""
    if not targets:
        return ""
    return json.dumps([outcome.model_dump(mode="json") for outcome in targets])


def _decode_targets(raw: Any) -> tuple[DeliveryOutcome, ...]:
    """Read back per-destination outcomes; unreadable rows degrade to empty."""
    text = str(raw or "").strip()
    if not text:
        return ()
    try:
        entries = json.loads(text)
        return tuple(DeliveryOutcome.model_validate(entry) for entry in entries)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.debug("Skipping unreadable per-target run outcomes", exc_info=True)
        return ()


def _row_to_task_run(row: tuple[Any, ...]) -> TaskRun:
    return TaskRun(
        task_id=row[0],
        fire_time=row[1],
        started_at=row[2],
        finished_at=row[3] or None,
        status=TaskStatus(row[4]),
        posted_message_id=row[5] or "",
        error=row[6] or "",
        provider=row[7] or "",
        targets=_decode_targets(row[8]),
    )


def get_runs(task_id: str, limit: int = 20, db_path: Path | None = None) -> list[TaskRun]:
    """Return recent runs for a task, newest first."""
    path = db_path or _default_db_path()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            "SELECT task_id, fire_time, started_at, finished_at, status, "
            "posted_message_id, error, provider, targets "
            "FROM task_runs WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        )
        return [_row_to_task_run(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_latest_finished_run(task_id: str, db_path: Path | None = None) -> TaskRun | None:
    """Return the most recently completed run for ``task_id``, if any.

    Orders by completion time, not start time, and ignores in-flight rows so a
    burst of pending claims cannot hide the last delivery outcome.
    """
    path = db_path or _default_db_path()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            "SELECT task_id, fire_time, started_at, finished_at, status, "
            "posted_message_id, error, provider, targets "
            "FROM task_runs WHERE task_id = ? AND status IN (?, ?) "
            "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1",
            (task_id, TaskStatus.SUCCESS.value, TaskStatus.FAILED.value),
        )
        row = cursor.fetchone()
        return _row_to_task_run(row) if row is not None else None
    finally:
        conn.close()


def get_latest_targeted_run(task_id: str, db_path: Path | None = None) -> TaskRun | None:
    """Return the most recent completed run that recorded per-target outcomes.

    Skips runs with no usable per-target history — one that failed before
    delivery (message build), one whose retry matched no destination, or one
    whose stored outcomes cannot be decoded. Those carry no information about
    what was delivered, so letting one shadow an earlier partial failure would
    strand a ``--failed-only`` retry: either widened back to every destination
    (re-posting where the message already landed) or narrowed to nothing.

    Emptiness is judged after decoding, not by the raw column: a non-empty but
    unreadable value decodes to no outcomes, so a SQL-level check alone would
    let it shadow a readable older run.
    """
    path = db_path or _default_db_path()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            "SELECT task_id, fire_time, started_at, finished_at, status, "
            "posted_message_id, error, provider, targets "
            "FROM task_runs WHERE task_id = ? AND status IN (?, ?) "
            "AND targets IS NOT NULL AND targets != '' "
            "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT ?",
            (
                task_id,
                TaskStatus.SUCCESS.value,
                TaskStatus.FAILED.value,
                _TARGETED_RUN_SCAN_LIMIT,
            ),
        )
        for row in cursor.fetchall():
            run = _row_to_task_run(row)
            if run.targets:
                return run
        return None
    finally:
        conn.close()


def delete_runs(task_id: str, db_path: Path | None = None) -> int:
    """Delete all task-run records for a given task ID.

    Returns the number of deleted rows. Safe to call when no DB or table
    exists (returns 0). Idempotent — subsequent calls return 0.
    """
    path = db_path or _default_db_path()
    if not path.exists():
        return 0
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            "DELETE FROM task_runs WHERE task_id = ?",
            (task_id,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


__all__ = [
    "complete_run",
    "delete_runs",
    "get_latest_finished_run",
    "get_latest_targeted_run",
    "get_runs",
    "start_queued_run",
    "try_claim",
    "try_queue_run",
    "try_start_run",
]
