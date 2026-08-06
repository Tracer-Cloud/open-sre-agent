"""Scheduler coverage for work-item reminders and check-ins."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from config.constants import OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.work_items import add_work_item, get_work_item
from platform.scheduler import tasks as tasks_mod
from platform.scheduler.executor import execute_task
from platform.scheduler.runner import _scheduled_job
from platform.scheduler.types import Provider, ScheduledTask, TaskKind


@pytest.fixture(autouse=True)
def _isolated_work_items_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work_items"))


def test_work_item_reminder_builds_message_without_marking_reminded() -> None:
    item = add_work_item(title="Ping owners", priority="high", remind_at="2026-07-30T09:00")
    task = ScheduledTask(
        kind=TaskKind.WORK_ITEM_REMINDER,
        cron="0 9 30 7 *",
        provider=Provider.SLACK,
        chat_id="C123",
        params={"work_item_id": item.id},
    )

    message = tasks_mod.build_message(task)

    assert "Ping owners" in message
    updated = get_work_item(item.id)
    assert updated is not None
    assert updated.last_reminded_at == ""


def test_work_item_reminder_marks_reminded_after_successful_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = add_work_item(title="Ping owners", priority="high", remind_at="2026-07-30T09:00")
    task = ScheduledTask(
        id="reminder-delivery",
        kind=TaskKind.WORK_ITEM_REMINDER,
        cron="0 9 30 7 *",
        provider=Provider.SLACK,
        chat_id="C123",
        params={"work_item_id": item.id},
    )
    monkeypatch.setattr(
        "platform.scheduler.executor._deliver_all",
        lambda _task, _message: (True, "", "msg-1"),
    )

    with (
        patch("platform.scheduler.executor.try_claim", return_value=True),
        patch("platform.scheduler.executor.complete_run"),
        patch("platform.scheduler.executor._emit_analytics"),
        patch("platform.scheduler.executor._emit_analytics_started"),
    ):
        assert execute_task(task, "2026-07-30T09:00") is True

    updated = get_work_item(item.id)
    assert updated is not None
    assert updated.last_reminded_at


def test_work_item_reminder_does_not_mark_reminded_when_delivery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = add_work_item(title="Ping owners", priority="high", remind_at="2026-07-30T09:00")
    task = ScheduledTask(
        id="reminder-fail",
        kind=TaskKind.WORK_ITEM_REMINDER,
        cron="0 9 30 7 *",
        provider=Provider.SLACK,
        chat_id="C123",
        params={"work_item_id": item.id},
    )
    monkeypatch.setattr(
        "platform.scheduler.executor._deliver_all",
        lambda _task, _message: (False, "boom", ""),
    )

    with (
        patch("platform.scheduler.executor.try_claim", return_value=True),
        patch("platform.scheduler.executor._record_failure"),
        patch("platform.scheduler.executor._emit_analytics_started"),
    ):
        assert execute_task(task, "2026-07-30T09:00") is False
    updated = get_work_item(item.id)
    assert updated is not None
    assert updated.last_reminded_at == ""


def test_work_item_reminder_skips_completed_items() -> None:
    item = add_work_item(title="Already done", remind_at="2026-07-30T09:00")
    item = get_work_item(item.id)
    assert item is not None
    from core.domain.work_items import complete_work_items

    complete_work_items([item.id])
    task = ScheduledTask(
        kind=TaskKind.WORK_ITEM_REMINDER,
        cron="0 9 30 7 *",
        provider=Provider.SLACK,
        chat_id="C123",
        params={"work_item_id": item.id},
    )

    assert tasks_mod.build_message(task) == ""


def test_work_item_checkin_builds_ranked_digest() -> None:
    add_work_item(title="Low polish", priority="low", project="hackathon")
    add_work_item(title="Fix blocker", priority="urgent", project="hackathon")
    task = ScheduledTask(
        kind=TaskKind.WORK_ITEM_CHECKIN,
        cron="0 9 * * 1-5",
        provider=Provider.SLACK,
        chat_id="C123",
        params={"project": "hackathon"},
    )

    message = tasks_mod.build_message(task)

    assert message.index("Fix blocker") < message.index("Low polish")


def test_scheduled_job_disables_one_shot_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    task = ScheduledTask(
        id="reminder1",
        kind=TaskKind.WORK_ITEM_REMINDER,
        cron="0 9 30 7 *",
        provider=Provider.SLACK,
        chat_id="C123",
        params={"disable_after_success": "true"},
    )
    saved: dict[str, ScheduledTask] = {}
    monkeypatch.setattr("platform.scheduler.runner.get_task", lambda _task_id: task)
    monkeypatch.setattr("platform.scheduler.runner.execute_task", lambda _task, _fire_time: True)
    monkeypatch.setattr(
        "platform.scheduler.runner.update_task", lambda updated: saved.setdefault("task", updated)
    )

    _scheduled_job("reminder1")

    assert saved["task"].enabled is False
    assert saved["task"].last_run
