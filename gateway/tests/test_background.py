from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from gateway.config.configure_gateway_logging import configure_gateway_logging
from gateway.config.get_gateway_settings import GatewaySettings
from gateway.core.telegram_gateway_background import start_telegram_gateway_background


def test_co_located_gateway_logging_does_not_propagate_to_root(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    configure_gateway_logging(co_located=True)
    logging.getLogger("gateway.platforms.telegram.poller").warning(
        "[telegram-gateway] getUpdates not ok: {}",
    )
    assert not any("getUpdates not ok" in record.message for record in caplog.records)


@patch("gateway.core.telegram_gateway_background.TelegramPoller")
@patch("gateway.core.telegram_gateway_background.GatewayRunner")
def test_start_starts_poll_thread(
    mock_runner: MagicMock, mock_poller_cls: MagicMock
) -> None:
    mock_poller_cls.return_value.poll_once.return_value = []
    logger = logging.getLogger("gateway.test")
    handle = start_telegram_gateway_background(
        settings=GatewaySettings(bot_token="tok"),
        logger=logger,
    )
    assert handle is not None
    handle.stop(timeout=1.0)
    mock_runner.assert_called_once()
