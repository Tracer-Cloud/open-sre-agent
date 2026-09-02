"""Tests for the task executor with isolated stores.

Delivery is inverted behind a per-provider adapter bundle. Routing/orchestration
tests install a fake bundle and configure each provider's outcome; adapter
behavior (creds resolution, the vendor transport call) is exercised by installing
the real bundle and patching that vendor's ``scheduled_delivery`` adapter.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import infrastructure.scheduling.scheduler.delivery_bundle as delivery_bundle
from config.constants import OPENSRE_OPERATIONS_LOG_PATH_ENV
from infrastructure.observability.operations_log import read_operations
from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.local_delivery import get_loop_messages
from infrastructure.scheduling.scheduler.loop_constants import LOOP_CHANNELS_PARAM
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tests.scheduler._bundle import real_runners

_DELIVERY_PROVIDERS = (
    Provider.TELEGRAM,
    Provider.SLACK,
    Provider.DISCORD,
    Provider.ROCKETCHAT,
    Provider.INTERACTIVE_SHELL,
)


class _FakeAdapter:
    """Records the (task, message) it is handed and returns a fixed outcome."""

    def __init__(self) -> None:
        self.result: tuple[bool, str, str] = (True, "", "msg")
        self.calls: list[tuple[ScheduledTask, str]] = []

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        self.calls.append((task, message))
        return self.result


def _install_fake_bundle() -> dict[Provider, _FakeAdapter]:
    """Install a fake adapter for every provider; return them to configure/inspect."""
    adapters = {provider: _FakeAdapter() for provider in _DELIVERY_PROVIDERS}
    delivery_bundle.ScheduledDeliveryAdapters(adapters).install()
    return adapters


def _install_real_bundle() -> None:
    """Install the production adapter bundle (exercises the real vendor adapters)."""
    from bootstrap.adapters import scheduled_delivery_adapters

    scheduled_delivery_adapters().install()


@pytest.fixture(autouse=True)
def _reset_delivery_bundle() -> None:
    """Drop the installed bundle after each test so it cannot leak across tests."""
    yield
    delivery_bundle._installed = None


@pytest.fixture()
def _tmp_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point both stores at tmp_path so tests are isolated."""
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.claim_store._default_db_path",
        lambda: tmp_path / "scheduler.db",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.store._default_store_path",
        lambda: tmp_path / "tasks.json",
    )


@pytest.mark.usefixtures("_tmp_stores")
class TestExecutor:
    def test_telegram_delivery_success(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.TELEGRAM].result = (True, "", "msg_42")
        task = ScheduledTask(
            id="test_tg_01",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert len(adapters[Provider.TELEGRAM].calls) == 1

    def test_telegram_missing_credentials(self) -> None:
        _install_real_bundle()
        task = ScheduledTask(
            id="test_tg_02",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch(
                "integrations.telegram.scheduled_delivery.resolve_telegram_credentials",
                return_value={},
            ),
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False

    def test_slack_delivery_success(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.SLACK].result = (True, "", "ts_123")
        task = ScheduledTask(
            id="test_sl_01",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123456",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert len(adapters[Provider.SLACK].calls) == 1

    def test_discord_delivery_success(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.DISCORD].result = (True, "", "msg_99")
        task = ScheduledTask(
            id="test_dc_01",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.DISCORD,
            chat_id="123456789",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert len(adapters[Provider.DISCORD].calls) == 1

    def test_rocketchat_delivery_success(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.ROCKETCHAT].result = (True, "", "msg_rc")
        task = ScheduledTask(
            id="test_rc_01",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.ROCKETCHAT,
            chat_id="#ops",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert len(adapters[Provider.ROCKETCHAT].calls) == 1

    def test_interactive_shell_delivery_success(self, tmp_path: Path) -> None:
        _install_real_bundle()
        inbox_path = tmp_path / "loop_messages.jsonl"
        task = ScheduledTask(
            id="test_shell_01",
            name="Local loop",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="<b>Scheduled</b> report",
            ),
            patch(
                "infrastructure.scheduling.scheduler.local_delivery._default_inbox_path",
                return_value=inbox_path,
            ),
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        messages = get_loop_messages(inbox_path=inbox_path)
        assert len(messages) == 1
        assert messages[0].name == "Local loop"
        assert messages[0].message == "Scheduled report"

    def test_execution_logs_operations_without_message_body(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.INTERACTIVE_SHELL].result = (True, "", "local:1")
        log_path = tmp_path / "operations.jsonl"
        monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(log_path))
        task = ScheduledTask(
            id="test_ops_log",
            name="Local loop",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Sensitive scheduled report body",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        records = read_operations(path=log_path)
        events = [record["event"] for record in records]
        assert events == [
            "scheduled_task_execution_started",
            "scheduled_task_execution_completed",
        ]
        completed = records[-1]["data"]
        assert completed["task_id"] == "test_ops_log"
        assert completed["fire_time"] == "2026-01-01T09:00"
        assert completed["status"] == "success"
        assert completed["message_chars"] == len("Sensitive scheduled report body")
        assert completed["message_id"] == "local:1"
        assert "Sensitive scheduled report body" not in json.dumps(records)

    def test_loop_fanout_builds_message_once(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.INTERACTIVE_SHELL].result = (True, "", "local:1")
        adapters[Provider.SLACK].result = (True, "", "ts_123")
        task = ScheduledTask(
            id="test_fanout",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_CHANNELS_PARAM: "interactive_shell,slack"},
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ) as mock_build:
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        mock_build.assert_called_once()
        assert len(adapters[Provider.INTERACTIVE_SHELL].calls) == 1
        assert len(adapters[Provider.SLACK].calls) == 1

    def test_loop_fanout_partial_success_completes_claim(self) -> None:
        """One channel failing must not leave an unrecoverable failed claim."""
        from infrastructure.scheduling.scheduler.claim_store import get_runs

        adapters = _install_fake_bundle()
        adapters[Provider.INTERACTIVE_SHELL].result = (True, "", "local:1")
        adapters[Provider.SLACK].result = (False, "webhook missing", "")
        task = ScheduledTask(
            id="test_fanout_partial",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_CHANNELS_PARAM: "interactive_shell,slack"},
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:05", real_runners())

        assert result is True
        runs = get_runs(task.id)
        assert len(runs) == 1
        assert runs[0].status.value == "success"
        assert "interactive_shell:local:1" in runs[0].posted_message_id
        assert "partial delivery" in runs[0].error
        assert "slack" in runs[0].error
        # First attempt + two retries for the failed slack destination.
        assert len(adapters[Provider.SLACK].calls) == 3
        assert len(adapters[Provider.INTERACTIVE_SHELL].calls) == 1

    def test_loop_fanout_slack_uses_default_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loop fan-out must resolve Slack chat_id when primary is interactive_shell."""
        adapters = _install_fake_bundle()
        task = ScheduledTask(
            id="test_fanout_slack_default",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_CHANNELS_PARAM: "interactive_shell,slack"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_default_chat_id",
            lambda _params: "C0123ABCD",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.executor.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        slack_task = adapters[Provider.SLACK].calls[-1][0]
        assert slack_task.chat_id == "C0123ABCD"

    def test_loop_fanout_slack_skips_default_when_webhook_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapters = _install_fake_bundle()
        task = ScheduledTask(
            id="test_fanout_slack_webhook",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_CHANNELS_PARAM: "slack"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_default_chat_id",
            lambda _params: "C0123ABCD",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.executor.resolve_slack_credentials",
            lambda _params: {"webhook_url": "https://hooks.slack.com/x"},
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Loop report",
        ):
            result = execute_task(task, "2026-01-01T10:00", real_runners())

        assert result is True
        slack_task = adapters[Provider.SLACK].calls[-1][0]
        assert slack_task.chat_id == ""

    def test_loop_fanout_slack_ignores_non_slack_task_chat_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Telegram chat id on an inbox-primary loop must not become Slack's channel."""
        adapters = _install_fake_bundle()
        task = ScheduledTask(
            id="test_fanout_slack_ignore_foreign_chat",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            chat_id="8098636622",
            params={LOOP_CHANNELS_PARAM: "slack"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_default_chat_id",
            lambda _params: "C0123ABCD",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.executor.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Loop report",
        ):
            result = execute_task(task, "2026-01-01T11:00", real_runners())

        assert result is True
        slack_task = adapters[Provider.SLACK].calls[-1][0]
        assert slack_task.chat_id == "C0123ABCD"

    def test_rocketchat_delivery_posts_to_channel(self) -> None:
        _install_real_bundle()
        task = ScheduledTask(
            id="test_rc_02",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.ROCKETCHAT,
            chat_id="#ops",
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="<b>Scheduled</b> report",
            ),
            patch(
                "integrations.rocketchat.scheduled_delivery.resolve_rocketchat_credentials",
                return_value={
                    "server_url": "https://chat.example.com",
                    "auth_token": "tok",
                    "user_id": "u1",
                },
            ),
            patch(
                "integrations.rocketchat.scheduled_delivery.post_rocketchat_message"
            ) as mock_post,
        ):
            mock_post.return_value = (True, "", "msg_rc")
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        args = mock_post.call_args.args
        assert args[0] == "https://chat.example.com"
        assert args[1] == "#ops"
        # HTML tags stripped — Rocket.Chat renders Markdown, not HTML.
        assert args[2] == "Scheduled report"
        assert args[3] == "tok"
        assert args[4] == "u1"

    def test_slack_delivery_fails_with_webhook_when_chat_id_set(self) -> None:
        """Webhook ignores chat_id — must not silently deliver to the wrong channel."""
        _install_real_bundle()
        task = ScheduledTask(
            id="test_sl_webhook_chat",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C0123ABCD",
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch(
                "integrations.slack.scheduled_delivery.resolve_slack_credentials",
                return_value={"webhook_url": "https://hooks.slack.com/services/T/B/x"},
            ),
            patch("integrations.slack.scheduled_delivery.send_slack_webhook_message") as mock_hook,
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False
        mock_hook.assert_not_called()

    def test_rocketchat_delivery_fails_without_token_credentials(self) -> None:
        _install_real_bundle()
        task = ScheduledTask(
            id="test_rc_03",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.ROCKETCHAT,
            chat_id="#ops",
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch(
                "integrations.rocketchat.scheduled_delivery.resolve_rocketchat_credentials",
                return_value={"webhook_url": "https://chat.example.com/hooks/a/b"},
            ),
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        # Webhook-only setups cannot honor the task's explicit chat_id.
        assert result is False

    def test_claim_dedup_prevents_double_execution(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.TELEGRAM].result = (True, "", "msg_1")
        task = ScheduledTask(
            id="test_dedup",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            # First execution succeeds
            result1 = execute_task(task, "2026-01-01T09:00", real_runners())
            # Second execution with same fire_time is deduped
            result2 = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result1 is True
        assert result2 is False
        # Only called once due to dedup
        assert len(adapters[Provider.TELEGRAM].calls) == 1

    def test_message_build_failure_records_error(self) -> None:
        task = ScheduledTask(
            id="test_fail",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch("infrastructure.scheduling.scheduler.executor.build_message") as mock_build:
            mock_build.side_effect = RuntimeError("Pipeline crashed")
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False

    def test_delivery_failure_records_error(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.TELEGRAM].result = (False, "Connection refused", "")
        task = ScheduledTask(
            id="test_del_fail",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False
        assert len(adapters[Provider.TELEGRAM].calls) == 1

    def test_delivery_targets_fan_out_same_message(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.SLACK].result = (True, "", "ts_123")
        adapters[Provider.TELEGRAM].result = (True, "", "msg_42")
        task = ScheduledTask(
            id="test_fanout",
            kind=TaskKind.WORK_ITEM_CHECKIN,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123",
            params={
                "delivery_targets": json.dumps(
                    [
                        {"provider": "slack", "chat_id": "C123"},
                        {"provider": "telegram", "chat_id": "-100123"},
                    ]
                )
            },
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        slack_call = adapters[Provider.SLACK].calls[-1]
        telegram_call = adapters[Provider.TELEGRAM].calls[-1]
        assert slack_call[0].chat_id == "C123"
        assert telegram_call[0].chat_id == "-100123"
        assert slack_call[1] == "Scheduled report"
        assert telegram_call[1] == "Scheduled report"

    def test_delivery_targets_report_partial_failure(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.SLACK].result = (True, "", "ts_123")
        adapters[Provider.TELEGRAM].result = (False, "missing token", "")
        task = ScheduledTask(
            id="test_fanout_fail",
            kind=TaskKind.WORK_ITEM_CHECKIN,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123",
            params={
                "delivery_targets": json.dumps(
                    [
                        {"provider": "slack", "chat_id": "C123"},
                        {"provider": "telegram", "chat_id": "-100123"},
                    ]
                )
            },
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False
        assert len(adapters[Provider.SLACK].calls) == 1
        assert len(adapters[Provider.TELEGRAM].calls) == 1

    def test_empty_message_skips_delivery(self) -> None:
        adapters = _install_fake_bundle()
        task = ScheduledTask(
            id="test_quiet_uptime",
            kind=TaskKind.SENTRY_UPTIME_WATCH,
            cron="*/5 * * * *",
            provider=Provider.SLACK,
            chat_id="C123",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert adapters[Provider.SLACK].calls == []
