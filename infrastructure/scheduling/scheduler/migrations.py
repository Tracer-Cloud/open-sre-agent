"""Schema changesets for the scheduler claim database."""

from __future__ import annotations

import sqlite3
import time

#: How long to keep retrying a column add while a competing process holds the
#: write lock. Each attempt re-reads the schema first, so a winner's commit
#: ends the loop immediately; this only bounds how long a stuck writer is
#: tolerated before the real error surfaces.
_MIGRATION_TIMEOUT_SECONDS = 30.0
_MIGRATION_RETRY_DELAY_SECONDS = 0.1

_TASK_RUNS_SCHEMA = """
    CREATE TABLE task_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        fire_time TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 1,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        posted_message_id TEXT DEFAULT '',
        error TEXT DEFAULT '',
        provider TEXT DEFAULT '',
        targets TEXT DEFAULT '',
        owner_token TEXT NOT NULL DEFAULT '',
        lease_expires_at TEXT NOT NULL DEFAULT '',
        UNIQUE(task_id, fire_time, attempt)
    )
"""


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Bring the scheduler claim schema to its current shape."""
    columns = _table_columns(conn)
    if {"attempt", "targets"} <= columns:
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = _table_columns(conn)
        if not columns:
            conn.execute(_TASK_RUNS_SCHEMA)
        elif "attempt" not in columns:
            _migrate_legacy_claim_table(conn, columns)
        _add_missing_columns(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _table_columns(conn: sqlite3.Connection, table: str = "task_runs") -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _has_targets_column(conn: sqlite3.Connection) -> bool:
    return "targets" in _table_columns(conn)


def _migrate_legacy_claim_table(conn: sqlite3.Connection, legacy_columns: set[str]) -> None:
    """Rebuild the pre-lease table while retaining its run history."""
    conn.execute("ALTER TABLE task_runs RENAME TO task_runs_legacy")
    conn.execute(_TASK_RUNS_SCHEMA)
    targets = "targets" if "targets" in legacy_columns else "''"
    conn.execute(
        "INSERT INTO task_runs "
        "(id, task_id, fire_time, attempt, started_at, finished_at, status, "
        "posted_message_id, error, provider, targets, owner_token, lease_expires_at) "
        f"SELECT id, task_id, fire_time, 1, started_at, finished_at, status, "
        f"posted_message_id, error, provider, {targets}, '', started_at "
        "FROM task_runs_legacy"
    )
    conn.execute("DROP TABLE task_runs_legacy")


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Add compatible columns, tolerating another process winning the race.

    A competing migration can commit after this process checks the schema but
    before its own ``ALTER TABLE`` completes. Rechecking after an error covers
    an already-committed winner; retrying covers a winner that still held the
    lock when this connection reached its busy timeout. The retry remains
    bounded so a genuinely stuck writer surfaces instead of blocking the
    scheduler forever.
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


__all__ = ["apply_migrations"]
