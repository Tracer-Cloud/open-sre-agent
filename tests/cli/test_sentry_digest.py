"""Tests for Sentry digest CLI prerequisites."""

from __future__ import annotations

from click.testing import CliRunner

from surfaces.cli.commands.sentry_digest import sentry_command


def test_schedule_add_requires_delivery_provider(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.configured_integration_services",
        lambda: ("sentry",),
    )
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.delivery_provider_ready",
        lambda _provider: False,
    )

    result = runner.invoke(
        sentry_command,
        [
            "digest",
            "schedule",
            "add",
            "--cron",
            "0 8 * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
        ],
    )

    assert result.exit_code == 1
    assert "Telegram is not configured" in result.output


def test_schedule_add_requires_sentry(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.configured_integration_services",
        lambda: ("telegram",),
    )

    result = runner.invoke(
        sentry_command,
        [
            "digest",
            "schedule",
            "add",
            "--cron",
            "0 8 * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
        ],
    )

    assert result.exit_code == 1
    assert "Sentry is not configured" in result.output
