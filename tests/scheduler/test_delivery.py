"""Tests for scheduler delivery readiness checks (provider-generic)."""

from __future__ import annotations

import pytest

from platform.scheduler.delivery import (
    any_delivery_ready,
    delivery_provider_ready,
    delivery_setup_hint,
    rocketchat_delivery_ready,
    slack_can_deliver,
    slack_delivery_ready,
    telegram_delivery_ready,
)
from platform.scheduler.types import Provider


class TestDeliveryReadiness:
    def test_telegram_ready_when_token_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {"bot_token": "token"},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        assert telegram_delivery_ready() is True
        assert delivery_provider_ready(Provider.TELEGRAM) is True
        assert any_delivery_ready() is True

    def test_slack_ready_with_bot_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-token"},
        )
        assert slack_delivery_ready() is True
        assert delivery_provider_ready("slack") is True

    def test_slack_not_ready_with_webhook_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A webhook posts to its own fixed channel and ignores the task's
        # chat_id, so a webhook-only install must not pass readiness for a
        # report that always carries an explicit --chat-id.
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_rocketchat_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {"webhook_url": "https://hooks.slack.com/services/x"},
        )
        assert slack_delivery_ready() is False
        assert delivery_provider_ready("slack") is False
        assert any_delivery_ready() is False

    def test_rocketchat_ready_with_full_token_trio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Isolate telegram/slack too: any_delivery_ready() must read
        # True because of Rocket.Chat specifically, not because a real
        # TELEGRAM_BOT_TOKEN/SLACK_WEBHOOK_URL happens to be set locally.
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_rocketchat_credentials",
            lambda _params: {
                "server_url": "https://chat.example.com",
                "auth_token": "tok",
                "user_id": "u1",
            },
        )
        assert rocketchat_delivery_ready() is True
        assert delivery_provider_ready(Provider.ROCKETCHAT) is True
        assert any_delivery_ready() is True

    def test_rocketchat_not_ready_with_webhook_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A webhook alone cannot target an explicit --chat-id destination."""
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_rocketchat_credentials",
            lambda _params: {"webhook_url": "https://chat.example.com/hooks/a/b"},
        )
        assert rocketchat_delivery_ready() is False
        assert delivery_provider_ready(Provider.ROCKETCHAT) is False
        assert any_delivery_ready() is False

    def test_none_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "platform.scheduler.delivery.resolve_rocketchat_credentials",
            lambda _params: {},
        )
        assert any_delivery_ready() is False
        assert "Telegram, Slack, or Rocket.Chat" in delivery_setup_hint()

    def test_provider_specific_hint(self) -> None:
        assert "Telegram" in delivery_setup_hint(Provider.TELEGRAM)
        assert "Slack" in delivery_setup_hint(Provider.SLACK)
        assert "Rocket.Chat" in delivery_setup_hint(Provider.ROCKETCHAT)

    def test_unknown_provider_hint(self) -> None:
        hint = delivery_setup_hint("discord")
        assert "discord" in hint
        assert "not a supported delivery provider" in hint
        assert "telegram" in hint

    def test_slack_can_deliver_policy(self) -> None:
        assert slack_can_deliver({"access_token": "xoxb"}, chat_id="C1") is True
        assert (
            slack_can_deliver({"webhook_url": "https://hooks.slack.com/x"}, chat_id="C1") is False
        )
        assert slack_can_deliver({"webhook_url": "https://hooks.slack.com/x"}, chat_id="") is True
        assert slack_can_deliver({}, chat_id="") is False
