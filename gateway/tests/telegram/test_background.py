from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from gateway.transports.telegram.background import start_telegram_gateway_background
from gateway.transports.telegram.runtime import (
    initialize_telegram_polling_runtime,
    shutdown_telegram_polling_runtime,
)
from gateway.transports.telegram.settings import GatewaySettings


@patch("gateway.transports.telegram.background.TelegramPoller")
def test_start_starts_poll_thread(mock_poller_cls: MagicMock) -> None:
    from gateway.transports.telegram.poller.poller import TelegramPollResult

    mock_poller_cls.return_value.poll_once.return_value = TelegramPollResult()
    logger = logging.getLogger("gateway.test")
    handle = start_telegram_gateway_background(
        settings=GatewaySettings(bot_token="tok"),
        logger=logger,
        initialize_runtime=initialize_telegram_polling_runtime,
        shutdown_runtime=shutdown_telegram_polling_runtime,
        handle_callback_to_gateway_agent=lambda *_args: None,
    )
    assert handle is not None
    handle.stop(timeout=1.0)
    mock_poller_cls.assert_called_once_with("tok")
