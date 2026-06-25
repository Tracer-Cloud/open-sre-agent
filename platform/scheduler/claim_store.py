"""SQLite-backed execution claims and run history.

The UNIQUE(task_id, fire_time) constraint prevents double-posting when
multiple scheduler instances race for the same tick.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from platform.scheduler.types import TaskRun, TaskStatus

from config.constants import OPENSRE_HOME_DIR

_DB_FILENAME = "scheduler.db"


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
            UNIQUE(task_id, fire_time)
        )
    """)
    conn.commit()


def try_claim(task_id: str, fire_time: str, db_path: Path | None = None) -> bool:
    """Attempt to claim a task execution slot.

    Returns True if this instance won the claim (INSERT succeeded).
    Returns False if another instance already claimed it (UNIQUE violation).
    """
    path = db_path or _default_db_path()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO task_runs (task_id, fire_time, started_at, status) "
            "VALUES (?, ?, ?, ?)",
            (task_id, fire_time, now, TaskStatus.RUNNING.value),
        )
        conn.commit()
        # rowcount == 1 means our INSERT went through; 0 means IGNORE fired
        return cursor.rowcount == 1
    except sqlite3.IntegrityError:
        return False
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
    db_path: Path | None = None,
) -> None:
    """Mark a claimed run as completed (success or failed)."""
    path = db_path or _default_db_path()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE task_runs SET finished_at = ?, status = ?, "
            "posted_message_id = ?, error = ?, provider = ? "
            "WHERE task_id = ? AND fire_time = ?",
            (now, status.value, posted_message_id, error, provider, task_id, fire_time),
        )
        conn.commit()
    finally:
        conn.close()


def get_runs(task_id: str, limit: int = 20, db_path: Path | None = None) -> list[TaskRun]:
    """Return recent runs for a task, newest first."""
    path = db_path or _default_db_path()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            "SELECT task_id, fire_time, started_at, finished_at, status, "
            "posted_message_id, error, provider "
            "FROM task_runs WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        )
        runs: list[TaskRun] = []
        for row in cursor.fetchall():
            runs.append(
                TaskRun(
                    task_id=row[0],
                    fire_time=row[1],
                    started_at=row[2],
                    finished_at=row[3] or None,
                    status=TaskStatus(row[4]),
                    posted_message_id=row[5] or "",
                    error=row[6] or "",
                    provider=row[7] or "",
                )
            )
        return runs
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
    "get_runs",
    "try_claim",
]
