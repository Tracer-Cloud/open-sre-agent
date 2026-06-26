"""Tests for the SQLite-backed claim store (dedup and run history)."""

from __future__ import annotations

from pathlib import Path

import pytest

from platform.scheduler.claim_store import complete_run, delete_runs, get_runs, try_claim
from platform.scheduler.types import TaskStatus


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "scheduler.db"


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
