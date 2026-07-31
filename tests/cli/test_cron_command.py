"""Tests for ``opensre cron`` CLI command input validation."""

from __future__ import annotations

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
