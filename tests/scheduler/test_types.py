"""Tests for scheduler domain models."""

from __future__ import annotations

from platform.scheduler.types import (
    Provider,
    ScheduledTask,
    TaskKind,
    TaskRun,
    TaskStatus,
)


class TestScheduledTask:
    def test_default_id_generated(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * 1-5",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )
        assert len(task.id) == 12
        assert task.id.isalnum()

    def test_display_id_truncates(self) -> None:
        task = ScheduledTask(
            id="abcdef123456",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
        )
        assert task.display_id() == "abcdef123456"

    def test_defaults(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
        )
        assert task.timezone == "UTC"
        assert task.window_hours == 24
        assert task.enabled is True
        assert task.params == {}
        assert task.last_run is None
        assert task.next_run is None

    def test_all_task_kinds(self) -> None:
        assert TaskKind.DAILY_SUMMARY == "daily_summary"
        assert TaskKind.WEEKLY_AUDIT == "weekly_audit"
        assert TaskKind.INCIDENT_WINDOW_REPLAY == "incident_window_replay"
        assert TaskKind.SYNTHETIC_RUN == "synthetic_run"
        assert TaskKind.CUSTOM_INVESTIGATION == "custom_investigation"

    def test_all_providers(self) -> None:
        assert Provider.TELEGRAM == "telegram"
        assert Provider.SLACK == "slack"
        assert Provider.DISCORD == "discord"


class TestTaskRun:
    def test_defaults(self) -> None:
        run = TaskRun(task_id="abc123", fire_time="2026-01-01T09:00")
        assert run.status == TaskStatus.PENDING
        assert run.posted_message_id == ""
        assert run.error == ""
        assert run.provider == ""

    def test_all_statuses(self) -> None:
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.SUCCESS == "success"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.SKIPPED == "skipped"
