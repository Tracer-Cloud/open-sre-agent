from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from gateway.config.get_gateway_settings import GatewaySettings
from gateway.polling.telegram_gateway_background import start_telegram_gateway_background
from gateway.polling.telegram_polling_runtime import (
    initialize_telegram_polling_runtime,
    shutdown_telegram_polling_runtime,
)


@patch("gateway.polling.telegram_gateway_background.TelegramPoller")
def test_start_starts_poll_thread(mock_poller_cls: MagicMock) -> None:
    mock_poller_cls.return_value.poll_once.return_value = []
    logger = logging.getLogger("gateway.test")
    handle = start_telegram_gateway_background(
        settings=GatewaySettings(bot_token="tok"),
        logger=logger,
        initialize_runtime=initialize_telegram_polling_runtime,
        shutdown_runtime=shutdown_telegram_polling_runtime,
    )
    assert handle is not None
    handle.stop(timeout=1.0)
    mock_poller_cls.assert_called_once_with("tok")
