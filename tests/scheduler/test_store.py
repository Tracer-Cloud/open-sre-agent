"""Tests for the JSON-backed task store."""

from __future__ import annotations

from pathlib import Path

import pytest

from platform.scheduler.claim_store import get_runs, try_claim
from platform.scheduler.store import (
    add_task,
    get_task,
    list_tasks,
    remove_task,
    update_task,
)
from platform.scheduler.types import Provider, ScheduledTask, TaskKind


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
    """A crash or a damaged file must not erase the operator's schedules.

    The store is the only record of what is scheduled: a torn write that
    truncates it, or a later write that lands on top of a file nobody could
    read, silently cancels every digest, report, and uptime watch.
    """

    @staticmethod
    def _task(name: str, cron: str) -> ScheduledTask:
        return ScheduledTask(
            name=name,
            kind=TaskKind.DAILY_SUMMARY,
            cron=cron,
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

    def test_failed_write_leaves_the_previous_tasks_intact(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange: one stored task, then make the final rename fail.
        add_task(self._task("digest-7", "0 7 * * *"), store_path)
        before = store_path.read_text(encoding="utf-8")

        def _failing_replace(src: str, dst: str) -> None:
            raise OSError("simulated crash during replace")

        monkeypatch.setattr("platform.scheduler.store.os.replace", _failing_replace)

        # Act
        with pytest.raises(OSError):
            add_task(self._task("digest-8", "0 8 * * *"), store_path)

        # Assert: the original file is byte-identical, not truncated.
        assert store_path.read_text(encoding="utf-8") == before
        assert [task.name for task in list_tasks(store_path)] == ["digest-7"]

    def test_failed_write_does_not_leak_a_temp_file(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        add_task(self._task("digest-7", "0 7 * * *"), store_path)

        def _failing_replace(src: str, dst: str) -> None:
            raise OSError("simulated crash during replace")

        monkeypatch.setattr("platform.scheduler.store.os.replace", _failing_replace)

        # Act
        with pytest.raises(OSError):
            add_task(self._task("digest-8", "0 8 * * *"), store_path)

        # Assert: a retry loop must not fill the directory with debris.
        assert list(store_path.parent.glob("*.tmp*")) == []

    def test_unreadable_store_is_preserved_rather_than_overwritten(self, store_path: Path) -> None:
        # Arrange: a store truncated by an earlier torn write.
        add_task(self._task("digest-7", "0 7 * * *"), store_path)
        full = store_path.read_text(encoding="utf-8")
        store_path.write_text(full[: len(full) // 2], encoding="utf-8")

        # Act
        add_task(self._task("digest-8", "0 8 * * *"), store_path)

        # Assert: the damaged file survives for recovery instead of vanishing.
        backups = list(store_path.parent.glob("scheduler_tasks.json.corrupt-*"))
        assert len(backups) == 1
        assert "digest-7" in backups[0].read_text(encoding="utf-8")
        assert [task.name for task in list_tasks(store_path)] == ["digest-8"]

    def test_non_list_json_is_treated_as_unreadable(self, store_path: Path) -> None:
        # Arrange: a JSON object where a list belongs is damage, not "no tasks".
        store_path.write_text('{"tasks": []}', encoding="utf-8")

        # Act
        add_task(self._task("digest-8", "0 8 * * *"), store_path)

        # Assert
        assert list(store_path.parent.glob("scheduler_tasks.json.corrupt-*"))
