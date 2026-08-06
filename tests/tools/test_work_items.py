"""Tests for durable work-item action tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_WORK_ITEMS_DIR_ENV
from core.tool_framework.tool_decorator import REGISTERED_TOOL_ATTR
from tests.tools.conftest import BaseToolContract
from tools.system.work_items import (
    work_task_add,
    work_task_complete,
    work_task_list,
    work_task_prioritize,
    work_task_schedule_checkin,
    work_task_update,
)


@pytest.fixture(autouse=True)
def _isolated_work_items_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work_items"))
    monkeypatch.setattr("tools.system.work_items.tool.add_scheduled_task", lambda task: task)
    monkeypatch.setattr("tools.system.work_items.tool.list_tasks", lambda: [])
    monkeypatch.setattr("tools.system.work_items.tool.update_task", lambda _task: True)


def _registered(fn: Any) -> Any:
    return getattr(fn, REGISTERED_TOOL_ATTR)


class TestWorkTaskAddContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered(work_task_add)


class TestWorkTaskListContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered(work_task_list)


class TestWorkTaskCompleteContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered(work_task_complete)


class TestWorkTaskUpdateContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered(work_task_update)


class TestWorkTaskPrioritizeContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered(work_task_prioritize)


class TestWorkTaskScheduleCheckinContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered(work_task_schedule_checkin)


def test_add_list_complete_flow() -> None:
    added = work_task_add(title="Finish hackathon demo", project="hackathon", priority="high")

    listed = work_task_list(project="hackathon")
    assert listed["total"] == 1
    assert listed["tasks"][0]["title"] == "Finish hackathon demo"

    completed = work_task_complete(selectors=[added["task"]["display_id"]])
    assert completed["status"] == "completed"
    assert work_task_list()["total"] == 0


def test_prioritize_prefers_due_urgent_work() -> None:
    work_task_add(title="Polish docs", priority="normal")
    work_task_add(title="Fix blocker", priority="urgent", due_at="2026-07-29T10:00:00Z")

    result = work_task_prioritize()

    assert result["recommendations"][0]["task"]["title"] == "Fix blocker"


def test_add_with_reminder_requires_delivery_target() -> None:
    result = work_task_add(title="Ping channel", remind_at="2026-07-30T09:00")

    assert result["error"] == "missing_delivery_target"


def test_add_with_reminder_schedules_when_target_present() -> None:
    result = work_task_add(
        title="Ping channel",
        remind_at="2026-07-30T09:00",
        channel_provider="slack",
        channel_id="C123",
    )

    assert result["status"] == "created"
    assert result["scheduled_task_id"]


def test_add_with_reminder_supports_multi_target_fanout() -> None:
    result = work_task_add(
        title="Ping every channel",
        remind_at="2026-07-30T09:00",
        channel_targets=[
            {"provider": "slack", "chat_id": "C123"},
            {"provider": "telegram", "chat_id": "-100"},
        ],
    )

    assert result["status"] == "created"
    assert [target["provider"] for target in result["task"]["channel_targets"]] == [
        "slack",
        "telegram",
    ]


def test_update_with_invalid_selector_is_structured() -> None:
    assert work_task_update(selector="nope", priority="high")["error"] == "not_found"


def test_update_reminder_disables_previous_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    from platform.scheduler.types import Provider, ScheduledTask, TaskKind

    created = work_task_add(
        title="Ping channel",
        remind_at="2026-07-30T09:00",
        channel_provider="slack",
        channel_id="C123",
    )
    item_id = created["task"]["id"]
    old_task = ScheduledTask(
        id="old-reminder",
        kind=TaskKind.WORK_ITEM_REMINDER,
        cron="0 9 30 7 *",
        provider=Provider.SLACK,
        chat_id="C123",
        enabled=True,
        params={"work_item_id": item_id},
    )
    saved: dict[str, ScheduledTask] = {}
    monkeypatch.setattr(
        "tools.system.work_items.tool.list_tasks",
        lambda: [old_task],
    )

    def _capture_update(task: ScheduledTask) -> bool:
        saved["task"] = task
        return True

    monkeypatch.setattr("tools.system.work_items.tool.update_task", _capture_update)

    result = work_task_update(
        selector=item_id,
        remind_at="2026-07-31T10:00",
        channel_provider="slack",
        channel_id="C123",
    )

    assert result["status"] == "updated"
    assert result["scheduled_task_id"]
    assert saved["task"].id == "old-reminder"
    assert saved["task"].enabled is False


def test_schedule_checkin_validates_delivery_target() -> None:
    result = work_task_schedule_checkin(cron="0 9 * * 1-5")

    assert result["error"] == "missing_delivery_target"


def test_schedule_checkin_creates_scheduler_task() -> None:
    result = work_task_schedule_checkin(
        cron="0 9 * * 1-5",
        provider="slack",
        chat_id="C123",
        project="hackathon",
    )

    assert result["status"] == "scheduled"
    assert result["provider"] == "slack"


def test_schedule_checkin_supports_multi_target_fanout() -> None:
    result = work_task_schedule_checkin(
        cron="0 9 * * 1-5",
        channel_targets=[
            {"provider": "slack", "chat_id": "C123"},
            {"provider": "telegram", "chat_id": "-100"},
        ],
        project="hackathon",
    )

    assert result["status"] == "scheduled"
    assert [target["provider"] for target in result["delivery_targets"]] == [
        "slack",
        "telegram",
    ]
