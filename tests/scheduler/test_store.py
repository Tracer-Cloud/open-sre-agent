"""Tests for the JSON-backed task store."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler.claim_store import get_runs, try_claim
from infrastructure.scheduling.scheduler.store import (
    add_task,
    get_task,
    list_tasks,
    remove_task,
    update_task,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "scheduler_tasks.json"


def _db_path(store_path: Path) -> Path:
    return store_path.with_name("scheduler.db")


class TestStore:
    def test_list_empty(self, store_path: Path) -> None:
        tasks = list_tasks(store_path)
        assert tasks == []

    def test_add_and_list(self, store_path: Path) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * 1-5",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )
        added = add_task(task, store_path)
        assert added.id == task.id

        tasks = list_tasks(store_path)
        assert len(tasks) == 1
        assert tasks[0].id == task.id
        assert tasks[0].kind == TaskKind.DAILY_SUMMARY

    def test_get_task(self, store_path: Path) -> None:
        task = ScheduledTask(
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
        )
        add_task(task, store_path)

        found = get_task(task.id, store_path)
        assert found is not None
        assert found.kind == TaskKind.WEEKLY_AUDIT

    def test_get_task_not_found(self, store_path: Path) -> None:
        assert get_task("nonexistent", store_path) is None

    def test_remove_task(self, store_path: Path) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        add_task(task, store_path)
        assert remove_task(task.id, store_path) is True
        assert list_tasks(store_path) == []

    def test_remove_task_cascade_deletes_runs(self, store_path: Path) -> None:
        """Removing a task must also remove its TaskRun records."""
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        add_task(task, store_path)

        db_path = _db_path(store_path)
        try_claim(task.id, "2026-01-01T09:00", db_path=db_path)
        try_claim(task.id, "2026-01-01T10:00", db_path=db_path)

        assert remove_task(task.id, store_path) is True

        assert get_runs(task.id, db_path=db_path) == []

    def test_remove_task_cascade_does_not_affect_other_tasks(self, store_path: Path) -> None:
        """Removing one task's runs must not delete another task's runs."""
        task_a = ScheduledTask(
            id="task-a",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        task_b = ScheduledTask(
            id="task-b",
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
        )
        add_task(task_a, store_path)
        add_task(task_b, store_path)

        db_path = _db_path(store_path)
        try_claim("task-a", "2026-01-01T09:00", db_path=db_path)
        try_claim("task-b", "2026-01-01T09:00", db_path=db_path)

        assert remove_task("task-a", store_path) is True

        assert get_runs("task-a", db_path=db_path) == []
        assert len(get_runs("task-b", db_path=db_path)) == 1

    def test_remove_nonexistent(self, store_path: Path) -> None:
        assert remove_task("nonexistent", store_path) is False

    def test_update_task(self, store_path: Path) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        add_task(task, store_path)

        task.enabled = False
        assert update_task(task, store_path) is True

        updated = get_task(task.id, store_path)
        assert updated is not None
        assert updated.enabled is False

    def test_update_nonexistent(self, store_path: Path) -> None:
        task = ScheduledTask(
            id="nonexistent",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
        )
        assert update_task(task, store_path) is False

    def test_multiple_tasks(self, store_path: Path) -> None:
        for i in range(3):
            task = ScheduledTask(
                kind=TaskKind.DAILY_SUMMARY,
                cron=f"{i} 9 * * *",
                provider=Provider.TELEGRAM,
                chat_id=f"-{i}",
            )
            add_task(task, store_path)

        tasks = list_tasks(store_path)
        assert len(tasks) == 3

    def test_corrupted_store_returns_empty(self, store_path: Path) -> None:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("not valid json", encoding="utf-8")
        tasks = list_tasks(store_path)
        assert tasks == []

    def test_store_with_invalid_entries_skips_them(self, store_path: Path) -> None:
        import json

        store_path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {"id": "valid1", "kind": "daily_summary", "cron": "0 9 * * *", "provider": "telegram"},
            {"invalid": "entry"},
        ]
        store_path.write_text(json.dumps(data), encoding="utf-8")
        tasks = list_tasks(store_path)
        assert len(tasks) == 1
        assert tasks[0].id == "valid1"


class TestAddTaskDeduplicates:
    """One confirmation, one schedule.

    A real install accumulated 37 byte-identical ``daily_summary`` rows because
    every confirmation inserted instead of matching an existing schedule.
    """

    @staticmethod
    def _daily_summary(**overrides: object) -> ScheduledTask:
        fields: dict[str, object] = {
            "kind": TaskKind.DAILY_SUMMARY,
            "cron": "0 8 * * 1-5",
            "timezone": "UTC",
            "provider": Provider.SLACK,
            "chat_id": "C0123ABCD",
        }
        fields.update(overrides)
        return ScheduledTask(**fields)  # type: ignore[arg-type]

    def test_identical_task_is_stored_once(self, store_path: Path) -> None:
        # Arrange
        first = add_task(self._daily_summary(), store_path)

        # Act: the user confirms the same schedule again.
        second = add_task(self._daily_summary(), store_path)

        # Assert: one row, and the caller gets the schedule that already exists
        # so any id it reports back stays valid.
        assert len(list_tasks(store_path)) == 1
        assert second.id == first.id

    def test_repeated_confirmations_do_not_accumulate(self, store_path: Path) -> None:
        # Arrange / Act: the observed failure, in miniature.
        for _ in range(10):
            add_task(self._daily_summary(), store_path)

        # Assert
        assert len(list_tasks(store_path)) == 1

    def test_a_different_destination_is_a_different_schedule(self, store_path: Path) -> None:
        # Arrange / Act
        add_task(self._daily_summary(chat_id="C0000AAA"), store_path)
        add_task(self._daily_summary(chat_id="C1111BBB"), store_path)

        # Assert: merging these would silently drop a report the user wanted.
        assert len(list_tasks(store_path)) == 2

    def test_a_different_schedule_time_is_a_different_task(self, store_path: Path) -> None:
        # Arrange / Act
        add_task(self._daily_summary(cron="0 8 * * 1-5"), store_path)
        add_task(self._daily_summary(cron="0 18 * * 1-5"), store_path)

        # Assert
        assert len(list_tasks(store_path)) == 2

    def test_different_params_stay_separate(self, store_path: Path) -> None:
        # Arrange / Act: same slot, different report configuration.
        add_task(self._daily_summary(params={"stats_period": "7d"}), store_path)
        add_task(self._daily_summary(params={"stats_period": "30d"}), store_path)

        # Assert
        assert len(list_tasks(store_path)) == 2


class TestStoreDurability:
    """Task-store failures preserve recoverable data and avoid torn writes."""

    @staticmethod
    def _task(hour: int) -> ScheduledTask:
        return ScheduledTask(
            name=f"digest-{hour}",
            kind=TaskKind.DAILY_SUMMARY,
            cron=f"0 {hour} * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )

    def test_failed_replacement_preserves_the_previous_store(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_task(self._task(7), store_path)
        before = store_path.read_bytes()

        def fail_replace(_source: str, _destination: Path) -> None:
            raise OSError("replacement failed")

        monkeypatch.setattr("os.replace", fail_replace)

        with pytest.raises(OSError, match="replacement failed"):
            add_task(self._task(8), store_path)

        assert store_path.read_bytes() == before
        assert [task.name for task in list_tasks(store_path)] == ["digest-7"]
        assert list(store_path.parent.glob(f"{store_path.name}.tmp*")) == []

    def test_add_quarantines_an_unreadable_store(
        self, store_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        corrupt_bytes = b'[{"id": "lost-before-crash"}'
        store_path.write_bytes(corrupt_bytes)

        with caplog.at_level(logging.ERROR):
            add_task(self._task(7), store_path)

        recovery_files = list(store_path.parent.glob(f"{store_path.name}.corrupt-*"))
        assert len(recovery_files) == 1
        assert recovery_files[0].read_bytes() == corrupt_bytes
        assert [task.name for task in list_tasks(store_path)] == ["digest-7"]
        assert any(
            record.levelno == logging.ERROR and str(recovery_files[0]) in record.message
            for record in caplog.records
        )

    def test_wrong_top_level_shape_is_quarantined(self, store_path: Path) -> None:
        corrupt_bytes = b'{"tasks": []}'
        store_path.write_bytes(corrupt_bytes)

        add_task(self._task(7), store_path)

        recovery_files = list(store_path.parent.glob(f"{store_path.name}.corrupt-*"))
        assert len(recovery_files) == 1
        assert recovery_files[0].read_bytes() == corrupt_bytes
        assert [task.name for task in list_tasks(store_path)] == ["digest-7"]

    def test_quarantine_failure_leaves_the_corrupt_store_untouched(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        corrupt_bytes = b"truncated task store"
        store_path.write_bytes(corrupt_bytes)

        def fail_replace(_source: str, _destination: Path) -> None:
            raise OSError("quarantine failed")

        monkeypatch.setattr("os.replace", fail_replace)

        with pytest.raises(OSError, match="quarantine failed"):
            add_task(self._task(7), store_path)

        assert store_path.read_bytes() == corrupt_bytes
        assert list(store_path.parent.glob(f"{store_path.name}.corrupt-*")) == []

    def test_repeated_quarantines_keep_distinct_recovery_copies(self, store_path: Path) -> None:
        first_corrupt_bytes = b"first torn write"
        second_corrupt_bytes = b"second torn write"

        store_path.write_bytes(first_corrupt_bytes)
        add_task(self._task(7), store_path)
        store_path.write_bytes(second_corrupt_bytes)
        add_task(self._task(8), store_path)

        recovery_files = sorted(store_path.parent.glob(f"{store_path.name}.corrupt-*"))
        assert len(recovery_files) == 2
        assert {path.read_bytes() for path in recovery_files} == {
            first_corrupt_bytes,
            second_corrupt_bytes,
        }

    def test_remove_and_update_do_not_rewrite_an_unreadable_store(self, store_path: Path) -> None:
        corrupt_bytes = b"not valid json"
        store_path.write_bytes(corrupt_bytes)
        task = self._task(7)

        assert remove_task(task.id, store_path) is False
        assert update_task(task, store_path) is False
        assert store_path.read_bytes() == corrupt_bytes
        assert list(store_path.parent.glob(f"{store_path.name}.corrupt-*")) == []


class TestReloadSignal:
    """Store mutations wake a running scheduler, so every caller path benefits."""

    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
        signals: list[bool] = []
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.reload_signal.request_scheduler_reload",
            lambda: signals.append(True),
        )
        return signals

    @staticmethod
    def _task() -> ScheduledTask:
        return ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

    def test_add_signals_reload(self, store_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        signals = self._capture(monkeypatch)
        add_task(self._task(), store_path)
        assert signals == [True]

    def test_duplicate_add_does_not_signal_again(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        signals = self._capture(monkeypatch)
        task = self._task()
        add_task(task, store_path)
        signals.clear()
        add_task(task, store_path)  # identical schedule → dedup, no change
        assert signals == []

    def test_remove_signals_reload(self, store_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        added = add_task(self._task(), store_path)
        signals = self._capture(monkeypatch)
        assert remove_task(added.id, store_path) is True
        assert signals == [True]

    def test_remove_missing_does_not_signal(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        signals = self._capture(monkeypatch)
        assert remove_task("does-not-exist", store_path) is False
        assert signals == []
