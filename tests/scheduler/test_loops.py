"""Tests for scheduled loop channel defaults and validation."""

from __future__ import annotations

import pytest

from infrastructure.scheduling.scheduler.loops import default_loop_channels, normalize_loop_channels
from infrastructure.scheduling.scheduler.types import Provider


class TestDefaultLoopChannels:
    def test_includes_slack_when_webhook_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"webhook_url": "https://hooks.slack.com/x"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_default_chat_id",
            lambda _params: "",
        )

        channels = default_loop_channels()

        assert Provider.SLACK in channels
        assert Provider.INTERACTIVE_SHELL in channels

    def test_includes_slack_when_bot_token_and_default_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "C0123ABCD",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_default_chat_id",
            lambda _params: "",
        )

        channels = default_loop_channels()

        assert Provider.SLACK in channels

    def test_omits_slack_when_bot_token_without_default_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_default_chat_id",
            lambda _params: "",
        )

        channels = default_loop_channels()

        assert Provider.SLACK not in channels


class TestNormalizeLoopChannels:
    def test_slack_ready_with_default_channel_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "C0123ABCD",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_default_chat_id",
            lambda _params: "",
        )

        channels = normalize_loop_channels(["slack"])

        assert channels == (Provider.SLACK,)

    def test_slack_rejected_without_webhook_or_default_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "",
        )

        with pytest.raises(ValueError, match="SLACK_DEFAULT_CHAT_ID"):
            normalize_loop_channels(["slack"])
