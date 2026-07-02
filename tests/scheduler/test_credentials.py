"""Tests for credential resolution."""

from __future__ import annotations

import pytest

from platform.scheduler.credentials import (
    resolve_discord_credentials,
    resolve_slack_credentials,
    resolve_telegram_credentials,
)


class TestTelegramCredentials:
    def test_from_params(self) -> None:
        creds = resolve_telegram_credentials({"bot_token": "from_params"})
        assert creds == {"bot_token": "from_params"}

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from_env")
        monkeypatch.setattr(
            "platform.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_telegram_credentials({})
        assert creds == {"bot_token": "from_env"}

    def test_empty_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setattr(
            "platform.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_telegram_credentials({})
        assert creds == {}


class TestSlackCredentials:
    def test_from_params(self) -> None:
        creds = resolve_slack_credentials({"access_token": "xoxb-from-params"})
        assert creds == {"access_token": "xoxb-from-params"}

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")
        monkeypatch.setattr(
            "platform.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {"access_token": "xoxb-from-env"}

    def test_from_env_access_token_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.setenv("SLACK_ACCESS_TOKEN", "xoxp-from-access-env")
        monkeypatch.setattr(
            "platform.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {"access_token": "xoxp-from-access-env"}

    def test_from_env_bot_token_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-primary")
        monkeypatch.setenv("SLACK_ACCESS_TOKEN", "xoxp-secondary")
        monkeypatch.setattr(
            "platform.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {"access_token": "xoxb-primary"}

    def test_empty_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_ACCESS_TOKEN", raising=False)
        monkeypatch.setattr(
            "platform.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {}


class TestDiscordCredentials:
    def test_from_params(self) -> None:
        creds = resolve_discord_credentials({"bot_token": "discord_from_params"})
        assert creds == {"bot_token": "discord_from_params"}

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord_from_env")
        monkeypatch.setattr(
            "platform.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_discord_credentials({})
        assert creds == {"bot_token": "discord_from_env"}

    def test_empty_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        monkeypatch.setattr(
            "platform.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_discord_credentials({})
        assert creds == {}
