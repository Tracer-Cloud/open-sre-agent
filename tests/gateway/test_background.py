from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from gateway.background import (
    _configure_co_located_gateway_logging,
    telegram_gateway_auto_start_enabled,
    try_start_telegram_gateway_background,
)
from gateway.config import GatewaySettings


def test_telegram_gateway_auto_start_enabled_defaults_true() -> None:
    assert telegram_gateway_auto_start_enabled() is True


def test_telegram_gateway_auto_start_can_be_disabled(monkeypatch: object) -> None:
    monkeypatch.setenv("TELEGRAM_GATEWAY_AUTO_START", "false")  # type: ignore[attr-defined]
    assert telegram_gateway_auto_start_enabled() is False


def test_co_located_gateway_logging_does_not_propagate_to_root(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    _configure_co_located_gateway_logging()
    logging.getLogger("gateway.platforms.telegram.poller").warning(
        "[telegram-gateway] getUpdates not ok: {}",
    )
    assert not any("getUpdates not ok" in record.message for record in caplog.records)


@patch("gateway.background.run_poll_loop")
@patch("gateway.background.load_gateway_settings")
def test_try_start_requires_bot_token(mock_settings: MagicMock, mock_poll: MagicMock) -> None:
    mock_settings.return_value = GatewaySettings(bot_token="")
    assert try_start_telegram_gateway_background() is None
    mock_poll.assert_not_called()


@patch("gateway.background.run_poll_loop")
@patch("gateway.background.load_gateway_settings")
def test_try_start_skips_webhook_mode(mock_settings: MagicMock, mock_poll: MagicMock) -> None:
    mock_settings.return_value = GatewaySettings(
        bot_token="tok",
        webhook_url="https://example.com/hook",
        webhook_secret="secret",
    )
    assert try_start_telegram_gateway_background() is None
    mock_poll.assert_not_called()


@patch("gateway.background.run_poll_loop")
@patch("gateway.background.load_gateway_settings")
def test_try_start_starts_poll_thread(mock_settings: MagicMock, mock_poll: MagicMock) -> None:
    mock_settings.return_value = GatewaySettings(bot_token="tok")
    handle = try_start_telegram_gateway_background()
    assert handle is not None
    handle.stop(timeout=1.0)
    mock_poll.assert_called_once()
