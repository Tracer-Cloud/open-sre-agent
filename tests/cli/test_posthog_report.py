"""Tests for PostHog metric-report CLI prerequisites (#3824)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from platform.scheduler.delivery import SUPPORTED_DELIVERY_PROVIDERS
from surfaces.cli.commands.posthog_report import _PROVIDER_CHOICES, posthog_command


def test_posthog_provider_choices_match_supported_providers_constant() -> None:
    """Discord has no report readiness/send path, so it must stay excluded."""
    assert set(_PROVIDER_CHOICES) == {p.value for p in SUPPORTED_DELIVERY_PROVIDERS}
    assert "discord" not in _PROVIDER_CHOICES


def test_schedule_add_requires_posthog_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: ("telegram",),
    )

    result = runner.invoke(
        posthog_command,
        [
            "report",
            "schedule",
            "add",
            "--cron",
            "0 8 * * 1",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
        ],
    )

    assert result.exit_code == 1
    assert "PostHog MCP is not configured" in result.output


def test_schedule_add_rejects_rest_only_posthog(monkeypatch: pytest.MonkeyPatch) -> None:
    """REST ``posthog`` alone cannot serve the MCP skill path."""
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: ("posthog", "telegram"),
    )

    result = runner.invoke(
        posthog_command,
        [
            "report",
            "schedule",
            "add",
            "--cron",
            "0 8 * * 1",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
        ],
    )

    assert result.exit_code == 1
    assert "PostHog MCP is not configured" in result.output


def test_schedule_add_requires_delivery_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: ("posthog_mcp",),
    )
    monkeypatch.setattr(
        "platform.scheduler.delivery.delivery_provider_ready",
        lambda _provider: False,
    )

    result = runner.invoke(
        posthog_command,
        [
            "report",
            "schedule",
            "add",
            "--cron",
            "0 8 * * 1",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
        ],
    )

    assert result.exit_code == 1
    assert "Telegram is not configured" in result.output


def test_schedule_add_creates_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: ("posthog_mcp", "telegram"),
    )
    monkeypatch.setattr(
        "platform.scheduler.delivery.delivery_provider_ready",
        lambda _provider: True,
    )
    monkeypatch.setattr(
        "platform.scheduler.store._default_store_path",
        lambda: tmp_path / "tasks.json",
    )

    result = runner.invoke(
        posthog_command,
        [
            "report",
            "schedule",
            "add",
            "--cron",
            "0 8 * * 1",
            "--tz",
            "Europe/London",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
            "--period",
            "30d",
            "--metrics",
            "dau,signups",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "created" in result.output.lower()
    assert "30d" in result.output
    assert "dau,signups" in result.output


def test_report_run_requires_posthog_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: (),
    )

    result = runner.invoke(posthog_command, ["report", "run"])

    assert result.exit_code == 1
    assert "PostHog MCP is not configured" in result.output
