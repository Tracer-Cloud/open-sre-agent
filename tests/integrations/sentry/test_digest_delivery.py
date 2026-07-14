"""Tests for Sentry digest delivery readiness checks."""

from __future__ import annotations

import pytest

from integrations.sentry.digest_delivery import (
    any_digest_delivery_ready,
    delivery_provider_ready,
    digest_delivery_setup_hint,
    slack_delivery_ready,
    telegram_delivery_ready,
)
from platform.scheduler.types import Provider


class TestDigestDeliveryReadiness:
    def test_telegram_ready_when_token_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.sentry.digest_delivery.resolve_telegram_credentials",
            lambda _params: {"bot_token": "token"},
        )
        monkeypatch.setattr(
            "integrations.sentry.digest_delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        assert telegram_delivery_ready() is True
        assert delivery_provider_ready(Provider.TELEGRAM) is True
        assert any_digest_delivery_ready() is True

    def test_slack_ready_with_webhook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.sentry.digest_delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "integrations.sentry.digest_delivery.resolve_slack_credentials",
            lambda _params: {"webhook_url": "https://hooks.slack.com/services/x"},
        )
        assert slack_delivery_ready() is True
        assert delivery_provider_ready("slack") is True

    def test_none_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.sentry.digest_delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "integrations.sentry.digest_delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        assert any_digest_delivery_ready() is False
        assert "Telegram or Slack" in digest_delivery_setup_hint()

    def test_provider_specific_hint(self) -> None:
        assert "Telegram" in digest_delivery_setup_hint(Provider.TELEGRAM)
        assert "Slack" in digest_delivery_setup_hint(Provider.SLACK)
