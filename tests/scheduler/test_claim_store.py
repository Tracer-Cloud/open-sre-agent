"""Tests for the SQLite-backed claim store (dedup and run history)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler import claim_store
from infrastructure.scheduling.scheduler.claim_store import (
    complete_run,
    delete_runs,
    get_latest_finished_run,
    get_latest_targeted_run,
    get_runs,
    try_claim,
)
from infrastructure.scheduling.scheduler.types import DeliveryOutcome, Provider, TaskStatus


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "scheduler.db"


class _AlterFailsConnection:
    """Wraps a real connection, failing only its ``ALTER TABLE`` statements.

    ``sqlite3.Connection.execute`` cannot be monkeypatched directly (it is a
    read-only C attribute), so this stands in for the connection when a test
    needs a schema write to fail for a reason unrelated to the column already
    existing (e.g. a genuine lock timeout).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, *args: object) -> object:
        if sql.strip().startswith("ALTER TABLE"):
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *args)


class TestClaimStore:
    def test_first_claim_succeeds(self, db_path: Path) -> None:
        assert try_claim("task1", "2026-01-01T09:00", db_path=db_path) is True

    def test_duplicate_claim_fails(self, db_path: Path) -> None:
        assert try_claim("task1", "2026-01-01T09:00", db_path=db_path) is True
        assert try_claim("task1", "2026-01-01T09:00", db_path=db_path) is False

    def test_different_fire_times_both_succeed(self, db_path: Path) -> None:
        assert try_claim("task1", "2026-01-01T09:00", db_path=db_path) is True
        assert try_claim("task1", "2026-01-01T10:00", db_path=db_path) is True

    def test_different_tasks_same_fire_time(self, db_path: Path) -> None:
        assert try_claim("task1", "2026-01-01T09:00", db_path=db_path) is True
        assert try_claim("task2", "2026-01-01T09:00", db_path=db_path) is True

    def test_complete_run_success(self, db_path: Path) -> None:
        try_claim("task1", "2026-01-01T09:00", db_path=db_path)
        complete_run(
            "task1",
            "2026-01-01T09:00",
            status=TaskStatus.SUCCESS,
            posted_message_id="msg123",
            provider="telegram",
            db_path=db_path,
        )
        runs = get_runs("task1", db_path=db_path)
        assert len(runs) == 1
        assert runs[0].status == TaskStatus.SUCCESS
        assert runs[0].posted_message_id == "msg123"
        assert runs[0].finished_at is not None

    def test_complete_run_failed(self, db_path: Path) -> None:
        try_claim("task1", "2026-01-01T09:00", db_path=db_path)
        complete_run(
            "task1",
            "2026-01-01T09:00",
            status=TaskStatus.FAILED,
            error="Connection timeout",
            provider="slack",
            db_path=db_path,
        )
        runs = get_runs("task1", db_path=db_path)
        assert len(runs) == 1
        assert runs[0].status == TaskStatus.FAILED
        assert runs[0].error == "Connection timeout"

    def test_get_runs_ordered_newest_first(self, db_path: Path) -> None:
        for i in range(5):
            fire_time = f"2026-01-01T0{i}:00"
            try_claim("task1", fire_time, db_path=db_path)
            complete_run(
                "task1",
                fire_time,
                status=TaskStatus.SUCCESS,
                db_path=db_path,
            )

        runs = get_runs("task1", db_path=db_path)
        assert len(runs) == 5
        # Newest first
        assert runs[0].fire_time == "2026-01-01T04:00"
        assert runs[-1].fire_time == "2026-01-01T00:00"

    def test_get_runs_respects_limit(self, db_path: Path) -> None:
        for i in range(10):
            fire_time = f"2026-01-01T{i:02d}:00"
            try_claim("task1", fire_time, db_path=db_path)
            complete_run("task1", fire_time, status=TaskStatus.SUCCESS, db_path=db_path)

        runs = get_runs("task1", limit=3, db_path=db_path)
        assert len(runs) == 3

    def test_get_runs_empty(self, db_path: Path) -> None:
        runs = get_runs("nonexistent", db_path=db_path)
        assert runs == []

    def test_get_latest_finished_run_ignores_newer_in_flight_starts(self, db_path: Path) -> None:
        # Arrange: one finished delivery, then six newer RUNNING claims. A
        # start-ordered lookback of five would drop the finished row.
        conn = claim_store._connect(db_path)
        try:
            claim_store._ensure_schema(conn)
            conn.execute(
                "INSERT INTO task_runs "
                "(task_id, fire_time, started_at, finished_at, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "task1",
                    "2026-08-05T09:00:00Z",
                    "2026-08-05T09:00:00Z",
                    "2026-08-05T09:05:00Z",
                    TaskStatus.SUCCESS.value,
                ),
            )
            for i in range(6):
                conn.execute(
                    "INSERT INTO task_runs "
                    "(task_id, fire_time, started_at, status) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        "task1",
                        f"2026-08-05T10:{i:02d}:00Z",
                        f"2026-08-05T10:{i:02d}:00Z",
                        TaskStatus.RUNNING.value,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        # Act
        run = get_latest_finished_run("task1", db_path=db_path)

        # Assert
        assert run is not None
        assert run.status == TaskStatus.SUCCESS
        assert run.fire_time == "2026-08-05T09:00:00Z"
        assert len(get_runs("task1", limit=5, db_path=db_path)) == 5
        assert all(
            r.status == TaskStatus.RUNNING for r in get_runs("task1", limit=5, db_path=db_path)
        )

    def test_delete_runs_removes_only_matching_task(self, db_path: Path) -> None:
        try_claim("task1", "2026-01-01T09:00", db_path=db_path)
        try_claim("task2", "2026-01-01T09:00", db_path=db_path)
        assert len(get_runs("task1", db_path=db_path)) == 1
        assert len(get_runs("task2", db_path=db_path)) == 1

        deleted = delete_runs("task1", db_path=db_path)
        assert deleted == 1

        # task1 runs are gone
        assert get_runs("task1", db_path=db_path) == []
        # task2 runs are untouched
        assert len(get_runs("task2", db_path=db_path)) == 1

    def test_delete_runs_idempotent(self, db_path: Path) -> None:
        try_claim("task1", "2026-01-01T09:00", db_path=db_path)
        assert delete_runs("task1", db_path=db_path) == 1
        assert delete_runs("task1", db_path=db_path) == 0

    def test_delete_runs_empty_db(self, db_path: Path) -> None:
        assert delete_runs("nonexistent", db_path=db_path) == 0

    def test_delete_runs_deletes_multiple_runs(self, db_path: Path) -> None:
        for i in range(3):
            fire_time = f"2026-01-01T{i:02d}:00"
            try_claim("task1", fire_time, db_path=db_path)

        assert delete_runs("task1", db_path=db_path) == 3
        assert get_runs("task1", db_path=db_path) == []


class TestConcurrency:
    """Verify the UNIQUE constraint prevents double-posting."""

    def test_concurrent_claims_only_one_wins(self, db_path: Path) -> None:
        """Simulate two instances racing for the same (task_id, fire_time)."""
        # First claim wins
        result1 = try_claim("task1", "2026-01-01T09:00", db_path=db_path)
        # Second claim loses (same key)
        result2 = try_claim("task1", "2026-01-01T09:00", db_path=db_path)

        assert result1 is True
        assert result2 is False

        # Only one run record exists
        runs = get_runs("task1", db_path=db_path)
        assert len(runs) == 1


class TestPerTargetOutcomes:
    """Fan-out run history keeps one row per destination, in plan order."""

    def test_target_outcomes_round_trip_in_the_order_written(self, db_path: Path) -> None:
        try_claim("task1", "2026-01-01T09:00", db_path=db_path)
        outcomes = (
            DeliveryOutcome(
                provider=Provider.INTERACTIVE_SHELL, ok=True, message_id="local:1", attempts=1
            ),
            DeliveryOutcome(
                provider=Provider.SLACK,
                chat_id="C123",
                ok=False,
                error="webhook missing",
                attempts=3,
            ),
        )
        complete_run(
            "task1",
            "2026-01-01T09:00",
            status=TaskStatus.SUCCESS,
            targets=outcomes,
            db_path=db_path,
        )

        assert get_runs("task1", db_path=db_path)[0].targets == outcomes

    def test_runs_without_target_outcomes_read_back_empty(self, db_path: Path) -> None:
        try_claim("task1", "2026-01-01T09:00", db_path=db_path)
        complete_run("task1", "2026-01-01T09:00", status=TaskStatus.SUCCESS, db_path=db_path)

        assert get_runs("task1", db_path=db_path)[0].targets == ()

    def test_database_created_before_the_targets_column_is_migrated(self, db_path: Path) -> None:
        """An existing scheduler.db must gain the column instead of erroring."""
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE task_runs (
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
        conn.execute(
            "INSERT INTO task_runs (task_id, fire_time, started_at, status) VALUES (?, ?, ?, ?)",
            ("legacy", "2026-01-01T08:00", "2026-01-01T08:00:00+00:00", TaskStatus.SUCCESS.value),
        )
        conn.commit()
        conn.close()

        assert try_claim("task1", "2026-01-01T09:00", db_path=db_path) is True
        complete_run(
            "task1",
            "2026-01-01T09:00",
            status=TaskStatus.SUCCESS,
            targets=(DeliveryOutcome(provider=Provider.SLACK, ok=True, message_id="ts_1"),),
            db_path=db_path,
        )

        assert get_runs("legacy", db_path=db_path)[0].targets == ()
        assert get_runs("task1", db_path=db_path)[0].targets[0].message_id == "ts_1"

    def test_a_concurrent_migration_landing_first_does_not_raise(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reproduces the race two processes hit on a legacy database.

        Both check the column, see it missing, and only then does one of them
        commit its ``ALTER TABLE`` — so the other's own pre-check was already
        stale by the time it runs its own ``ALTER TABLE``. Forcing only the
        pre-check to lie (report "missing" when it is not) reproduces that
        stale window deterministically; a raw sequential call can't, since the
        second call's pre-check would correctly see the already-migrated
        schema and skip the write entirely.
        """
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE task_runs (
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
        # A concurrent connection's migration already landed by the time this
        # connection's own ALTER TABLE runs.
        conn.execute("ALTER TABLE task_runs ADD COLUMN targets TEXT DEFAULT ''")
        conn.commit()

        real_has_column = claim_store._has_targets_column
        calls = {"count": 0}

        def _lie_on_first_call(check_conn: sqlite3.Connection) -> bool:
            calls["count"] += 1
            # Pre-check: stale, as it genuinely was at that moment. The
            # post-failure recheck must see the truth or this test proves
            # nothing about the recovery path.
            return False if calls["count"] == 1 else real_has_column(check_conn)

        monkeypatch.setattr(claim_store, "_has_targets_column", _lie_on_first_call)

        claim_store._add_missing_columns(conn)  # must not raise
        assert calls["count"] == 2

    def test_a_winner_committing_after_our_timeout_is_picked_up_on_retry(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The window a single post-timeout recheck cannot see.

        Our ``ALTER TABLE`` times out while the competing migration is still
        uncommitted, so the schema recheck right after the failure genuinely
        finds nothing. The winner commits a moment later; the next attempt
        re-reads the schema, sees the column, and returns without a second
        write or a raised error.
        """
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE task_runs (
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

        # False for the pre-check and the post-timeout recheck (the winner has
        # not committed yet), then True once it lands during the retry delay.
        checks = {"count": 0}

        def _column_appears_on_the_retry(_conn: sqlite3.Connection) -> bool:
            checks["count"] += 1
            return checks["count"] > 2

        monkeypatch.setattr(claim_store, "_has_targets_column", _column_appears_on_the_retry)

        claim_store._add_missing_columns(_AlterFailsConnection(conn))  # must not raise

        # Pre-check, post-timeout recheck, then the retry's own pre-check.
        assert checks["count"] == 3

    def test_a_genuine_alter_failure_still_raises(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A writer stuck past the budget surfaces the real error rather than
        retrying forever."""
        monkeypatch.setattr(claim_store, "_MIGRATION_TIMEOUT_SECONDS", 0.2)
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE task_runs (
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

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            claim_store._add_missing_columns(_AlterFailsConnection(conn))


class TestLatestTargetedRun:
    """``--failed-only`` must not be widened by a run that recorded no targets."""

    def _finish(
        self,
        db_path: Path,
        fire_time: str,
        *,
        targets: tuple[DeliveryOutcome, ...],
        status: TaskStatus = TaskStatus.SUCCESS,
    ) -> None:
        try_claim("task1", fire_time, db_path=db_path)
        complete_run("task1", fire_time, status=status, targets=targets, db_path=db_path)

    def test_a_later_run_without_targets_does_not_shadow_the_partial_failure(
        self, db_path: Path
    ) -> None:
        """The bug: a retry matching nothing (or a build failure) records no
        outcomes, and treating that as "no history" sends everywhere again."""
        partial = (
            DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=True, message_id="ts_1"),
            DeliveryOutcome(provider=Provider.TELEGRAM, chat_id="-100", ok=False, error="no token"),
        )
        self._finish(db_path, "2026-01-01T09:00", targets=partial)
        # A --failed-only retry that matched no destination, recorded after it.
        self._finish(db_path, "2026-01-01T09:05", targets=(), status=TaskStatus.FAILED)

        run = get_latest_targeted_run("task1", db_path=db_path)

        assert run is not None
        assert run.targets == partial

    def test_no_run_with_targets_returns_none(self, db_path: Path) -> None:
        self._finish(db_path, "2026-01-01T09:00", targets=(), status=TaskStatus.FAILED)

        assert get_latest_targeted_run("task1", db_path=db_path) is None

    def test_the_newest_targeted_run_wins(self, db_path: Path) -> None:
        older = (DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=False, error="old"),)
        newer = (DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=True, message_id="ok"),)
        self._finish(db_path, "2026-01-01T09:00", targets=older)
        self._finish(db_path, "2026-01-01T09:05", targets=newer)

        run = get_latest_targeted_run("task1", db_path=db_path)

        assert run is not None
        assert run.targets == newer

    def test_an_unreadable_later_run_does_not_shadow_the_partial_failure(
        self, db_path: Path
    ) -> None:
        """A corrupt outcomes column is non-empty in SQL but decodes to nothing.

        Letting it win would hand ``--failed-only`` an empty filter, so the
        retry would deliver nowhere instead of to the destinations that failed.
        """
        partial = (
            DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=True, message_id="ts_1"),
            DeliveryOutcome(provider=Provider.TELEGRAM, chat_id="-100", ok=False, error="no token"),
        )
        self._finish(db_path, "2026-01-01T09:00", targets=partial)
        self._finish(db_path, "2026-01-01T09:05", targets=(), status=TaskStatus.FAILED)
        # Corrupt the newer row's history behind the encoder's back.
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE task_runs SET targets = ? WHERE task_id = ? AND fire_time = ?",
            ("{not json", "task1", "2026-01-01T09:05"),
        )
        conn.commit()
        conn.close()

        run = get_latest_targeted_run("task1", db_path=db_path)

        assert run is not None
        assert run.targets == partial

    def test_only_unreadable_history_returns_none(self, db_path: Path) -> None:
        self._finish(db_path, "2026-01-01T09:00", targets=(), status=TaskStatus.FAILED)
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE task_runs SET targets = ? WHERE task_id = ?", ("[[[", "task1"))
        conn.commit()
        conn.close()

        assert get_latest_targeted_run("task1", db_path=db_path) is None
