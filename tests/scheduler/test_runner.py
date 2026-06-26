"""Tests for the scheduler runner."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from platform.scheduler.runner import (
    _compute_fire_time,
    _make_trigger,
    _on_job_submitted,
    _pending_fire_times,
    run_task_now,
)
from platform.scheduler.types import Provider, ScheduledTask, TaskKind


class TestMakeTrigger:
    def test_valid_cron(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * 1-5",
            timezone="UTC",
            provider=Provider.TELEGRAM,
        )
        trigger = _make_trigger(task)
        assert trigger is not None

    def test_invalid_cron_too_few_fields(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 *",
            timezone="UTC",
            provider=Provider.TELEGRAM,
        )
        with pytest.raises(ValueError, match="5 fields"):
            _make_trigger(task)

    def test_invalid_cron_bad_values(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="61 25 * * *",
            timezone="UTC",
            provider=Provider.TELEGRAM,
        )
        with pytest.raises(ValueError):
            _make_trigger(task)

    def test_invalid_timezone(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            timezone="Invalid/Timezone",
            provider=Provider.TELEGRAM,
        )
        with pytest.raises(ValueError):
            _make_trigger(task)

    def test_valid_timezone(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * 1-5",
            timezone="Europe/London",
            provider=Provider.TELEGRAM,
        )
        trigger = _make_trigger(task)
        assert trigger is not None


class TestOnJobSubmitted:
    def test_stores_fire_time_from_scheduled_run_times(self) -> None:
        from datetime import UTC, datetime
        from types import SimpleNamespace

        _pending_fire_times.clear()
        event = SimpleNamespace(
            job_id="task-1",
            scheduled_run_times=[datetime(2026, 1, 15, 9, 0, tzinfo=UTC)],
        )
        _on_job_submitted(event)
        assert _pending_fire_times["task-1"] == "2026-01-15T09:00Z"


class TestComputeFireTime:
    def test_with_utc_datetime(self) -> None:
        from datetime import UTC, datetime

        dt = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
        result = _compute_fire_time(dt)
        assert result == "2026-01-15T09:00Z"

    def test_with_non_utc_datetime(self) -> None:
        from datetime import datetime, timedelta, timezone

        # UTC+5:30
        tz = timezone(timedelta(hours=5, minutes=30))
        dt = datetime(2026, 1, 15, 14, 30, tzinfo=tz)
        result = _compute_fire_time(dt)
        # 14:30 IST = 09:00 UTC
        assert result == "2026-01-15T09:00Z"

    def test_with_none_falls_back_to_utc_now(self) -> None:
        result = _compute_fire_time(None)
        assert result.endswith("Z")
        assert "T" in result


class TestRunTaskNow:
    def test_nonexistent_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "platform.scheduler.runner.get_task",
            lambda _task_id: None,
        )
        assert run_task_now("nonexistent") is False

    def test_runs_existing_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            id="run_now_test",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        monkeypatch.setattr("platform.scheduler.runner.get_task", lambda _task_id: task)

        with patch("platform.scheduler.runner.execute_task") as mock_exec:
            mock_exec.return_value = True
            result = run_task_now("run_now_test")

        assert result is True
        mock_exec.assert_called_once()
        # Verify fire_time has seconds (ad-hoc format) and ends with Z
        call_args = mock_exec.call_args
        fire_time = call_args[0][1]
        assert fire_time.endswith("Z")
        assert "T" in fire_time
        # Ad-hoc runs use second-precision to avoid colliding with scheduled runs
        assert len(fire_time.split("T")[1].rstrip("Z").split(":")) == 3
