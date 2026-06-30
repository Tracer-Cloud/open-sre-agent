from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from gateway.config.configure_gateway_logging import configure_gateway_logging
from gateway.config.get_gateway_settings import GatewaySettings
from gateway.core.telegram_gateway_background import start_telegram_gateway_background
from gateway.start_gateway import (
    initialize_telegram_polling_runtime,
    shutdown_telegram_polling_runtime,
)


def test_co_located_gateway_logging_does_not_propagate_to_root(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    configure_gateway_logging(co_located=True)
    logging.getLogger("gateway.core.telegram_poller.poller").warning(
        "[telegram-gateway] getUpdates not ok: {}",
    )
    assert not any("getUpdates not ok" in record.message for record in caplog.records)


@patch("gateway.core.telegram_gateway_background.TelegramPoller")
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
