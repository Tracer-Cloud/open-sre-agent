"""Tests for the task executor with isolated stores."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from platform.scheduler.executor import execute_task
from platform.scheduler.types import Provider, ScheduledTask, TaskKind


@pytest.fixture()
def _tmp_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point both stores at tmp_path so tests are isolated."""
    monkeypatch.setattr(
        "platform.scheduler.claim_store._default_db_path",
        lambda: tmp_path / "scheduler.db",
    )
    monkeypatch.setattr(
        "platform.scheduler.store._default_store_path",
        lambda: tmp_path / "tasks.json",
    )


@pytest.mark.usefixtures("_tmp_stores")
class TestExecutor:
    def test_telegram_delivery_success(self) -> None:
        task = ScheduledTask(
            id="test_tg_01",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch(
                "platform.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch("platform.scheduler.executor.resolve_telegram_credentials") as mock_creds,
            patch("platform.scheduler.executor._deliver_telegram") as mock_deliver,
        ):
            mock_creds.return_value = {"bot_token": "fake_token"}
            mock_deliver.return_value = (True, "", "msg_42")

            result = execute_task(task, "2026-01-01T09:00")

        assert result is True
        mock_deliver.assert_called_once()

    def test_telegram_missing_credentials(self) -> None:
        task = ScheduledTask(
            id="test_tg_02",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch(
                "platform.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch("platform.scheduler.executor.resolve_telegram_credentials") as mock_creds,
        ):
            mock_creds.return_value = {}
            result = execute_task(task, "2026-01-01T09:00")

        assert result is False

    def test_slack_delivery_success(self) -> None:
        task = ScheduledTask(
            id="test_sl_01",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123456",
        )

        with (
            patch(
                "platform.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch("platform.scheduler.executor._deliver_slack") as mock_deliver,
        ):
            mock_deliver.return_value = (True, "", "ts_123")
            result = execute_task(task, "2026-01-01T09:00")

        assert result is True
        mock_deliver.assert_called_once()

    def test_discord_delivery_success(self) -> None:
        task = ScheduledTask(
            id="test_dc_01",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.DISCORD,
            chat_id="123456789",
        )

        with (
            patch(
                "platform.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch("platform.scheduler.executor._deliver_discord") as mock_deliver,
        ):
            mock_deliver.return_value = (True, "", "msg_99")
            result = execute_task(task, "2026-01-01T09:00")

        assert result is True
        mock_deliver.assert_called_once()

    def test_claim_dedup_prevents_double_execution(self) -> None:
        task = ScheduledTask(
            id="test_dedup",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch(
                "platform.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch("platform.scheduler.executor._deliver_telegram") as mock_deliver,
        ):
            mock_deliver.return_value = (True, "", "msg_1")

            # First execution succeeds
            result1 = execute_task(task, "2026-01-01T09:00")
            # Second execution with same fire_time is deduped
            result2 = execute_task(task, "2026-01-01T09:00")

        assert result1 is True
        assert result2 is False
        # Only called once due to dedup
        assert mock_deliver.call_count == 1

    def test_message_build_failure_records_error(self) -> None:
        task = ScheduledTask(
            id="test_fail",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch("platform.scheduler.executor.build_message") as mock_build:
            mock_build.side_effect = RuntimeError("Pipeline crashed")
            result = execute_task(task, "2026-01-01T09:00")

        assert result is False

    def test_delivery_failure_records_error(self) -> None:
        task = ScheduledTask(
            id="test_del_fail",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch(
                "platform.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch("platform.scheduler.executor._deliver_telegram") as mock_deliver,
        ):
            mock_deliver.return_value = (False, "Connection refused", "")
            result = execute_task(task, "2026-01-01T09:00")

        assert result is False
        mock_deliver.assert_called_once()
