"""Tests for ``opensre cron`` CLI command input validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from platform.scheduler.types import Provider, TaskKind
from surfaces.cli.commands.cron import _KIND_CHOICES, _PROVIDER_CHOICES, cron_command


def test_cron_add_provider_choices_match_full_provider_enum() -> None:
    """cron delivery genuinely supports every Provider member."""
    assert set(_PROVIDER_CHOICES) == {p.value for p in Provider}


def test_cron_add_kind_choices_exclude_sentry_kinds() -> None:
    """Sentry-kind tasks go through `opensre sentry`, not generic cron add."""
    assert set(_KIND_CHOICES) == {k.value for k in TaskKind} - {
        TaskKind.SENTRY_MORNING_DIGEST.value,
        TaskKind.SENTRY_UPTIME_WATCH.value,
    }


def test_cron_add_rejects_non_positive_window() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--kind",
            "daily_summary",
            "--cron",
            "0 9 * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "-100123",
            "--window",
            "0",
        ],
    )
    assert result.exit_code != 0
    assert "not in the range" in result.output


def test_cron_logs_rejects_non_positive_limit() -> None:
    runner = CliRunner()
    result = runner.invoke(cron_command, ["logs", "task-123", "--limit", "0"])
    assert result.exit_code != 0
    assert "not in the range" in result.output


def test_cron_add_allows_slack_without_chat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Webhook-bound Slack delivery does not need --chat-id (morning-report yes).

    The webhook is the destination, so it must actually be configured — without
    one a bot-token install would store a task that delivers nowhere.
    """
    from platform.scheduler import store as scheduler_store

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/services/T/B/x")

    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--kind",
            "daily_summary",
            "--cron",
            "0 8 * * 1-5",
            "--tz",
            "Europe/Amsterdam",
            "--provider",
            "slack",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "created" in result.output.lower()


def test_cron_add_persists_loop_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from platform.scheduler import store as scheduler_store
    from platform.scheduler.store import list_tasks

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/services/T/B/x")

    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--name",
            "Morning report",
            "--kind",
            "daily_summary",
            "--cron",
            "0 8 * * 1-5",
            "--provider",
            "slack",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Name: Morning report" in result.output
    assert list_tasks(store)[0].name == "Morning report"


def test_cron_add_allows_interactive_shell_without_chat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from platform.scheduler import store as scheduler_store
    from platform.scheduler.store import list_tasks

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store)

    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--name",
            "Local loop",
            "--kind",
            "daily_summary",
            "--cron",
            "0 8 * * 1-5",
            "--provider",
            "interactive_shell",
        ],
    )

    assert result.exit_code == 0, result.output
    assert list_tasks(store)[0].provider == Provider.INTERACTIVE_SHELL


def test_cron_add_still_requires_chat_id_for_telegram() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--kind",
            "daily_summary",
            "--cron",
            "0 8 * * 1-5",
            "--provider",
            "telegram",
        ],
    )
    assert result.exit_code == 2
    assert "--chat-id is required" in result.output
