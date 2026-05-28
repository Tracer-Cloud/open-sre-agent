"""Tests for the JSON-backed task store."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.scheduler.store import add_task, get_task, list_tasks, remove_task, update_task
from app.scheduler.types import Provider, ScheduledTask, TaskKind


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "scheduler_tasks.json"


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
