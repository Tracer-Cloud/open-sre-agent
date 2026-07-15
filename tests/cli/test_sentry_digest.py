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


def test_uptime_watch_add_sends_activation_notice(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.configured_integration_services",
        lambda: ("sentry", "telegram"),
    )
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.delivery_provider_ready",
        lambda _provider: True,
    )
    monkeypatch.setattr(
        "platform.scheduler.store._default_store_path",
        lambda: tmp_path / "tasks.json",
    )
    delivered: list[str] = []

    def _fake_deliver(task, message: str):
        delivered.append(message)
        return True, "", "1"

    monkeypatch.setattr(
        "platform.scheduler.executor.deliver_scheduled_message",
        _fake_deliver,
    )

    result = runner.invoke(
        sentry_command,
        [
            "uptime",
            "watch",
            "add",
            "--cron",
            "*/5 * * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "8117261725",
        ],
    )

    assert result.exit_code == 0
    assert "Activation notice sent" in result.output
    assert delivered
    assert "active" in delivered[0].lower()
    assert "down" in delivered[0].lower()


def test_uptime_watch_add_requires_sentry(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.configured_integration_services",
        lambda: ("telegram",),
    )

    result = runner.invoke(
        sentry_command,
        [
            "uptime",
            "watch",
            "add",
            "--cron",
            "*/5 * * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
        ],
    )

    assert result.exit_code == 1
    assert "Sentry is not configured" in result.output


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
