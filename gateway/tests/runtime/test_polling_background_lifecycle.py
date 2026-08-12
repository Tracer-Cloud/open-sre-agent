"""Characterization tests for poll-transport background lifecycle."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.transports.buzz import background as buzz_background
from gateway.transports.buzz.settings import GatewaySettings as BuzzGatewaySettings
from gateway.transports.telegram import background as telegram_background
from gateway.transports.telegram.settings import GatewaySettings as TelegramGatewaySettings


@dataclass(frozen=True)
class _TransportCase:
    module: ModuleType
    start: Callable[..., Any]
    poll_name: str
    settings: object
    fatal_message: str


_CASES = (
    _TransportCase(
        module=telegram_background,
        start=telegram_background.start_telegram_gateway_background,
        poll_name="_poll_telegram_until_stopped",
        settings=TelegramGatewaySettings(bot_token="tok"),
        fatal_message="Fatal error in Telegram gateway thread",
    ),
    _TransportCase(
        module=buzz_background,
        start=buzz_background.start_buzz_gateway_background,
        poll_name="_poll_buzz_until_stopped",
        settings=BuzzGatewaySettings(private_key="key"),
        fatal_message="Fatal error in Buzz gateway thread",
    ),
)


@pytest.mark.parametrize("case", _CASES)
def test_stop_wakes_polling_thread_and_shuts_down_runtime(
    case: _TransportCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    runtime = object()
    shutdown_runtime = MagicMock()

    async def _poll_until_stopped(**kwargs: Any) -> None:
        stop_event = kwargs["stop_event"]
        started.set()
        await asyncio.to_thread(stop_event.wait)

    monkeypatch.setattr(case.module, case.poll_name, _poll_until_stopped)

    handle = case.start(
        settings=case.settings,
        logger=logging.getLogger("gateway.test.polling-lifecycle"),
        initialize_runtime=lambda _settings: runtime,
        shutdown_runtime=shutdown_runtime,
        handle_callback_to_gateway_agent=lambda *_args: None,
    )

    assert started.wait(timeout=1.0)
    assert handle.stop(timeout=1.0)
    shutdown_runtime.assert_called_once_with(runtime)


@pytest.mark.parametrize("case", _CASES)
def test_fatal_polling_error_is_logged_and_runtime_is_shut_down(
    case: _TransportCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = object()
    logger = MagicMock(spec=logging.Logger)
    shutdown_runtime = MagicMock()

    async def _raise_fatal_error(**_kwargs: Any) -> None:
        raise RuntimeError("polling failed")

    monkeypatch.setattr(case.module, case.poll_name, _raise_fatal_error)

    handle = case.start(
        settings=case.settings,
        logger=logger,
        initialize_runtime=lambda _settings: runtime,
        shutdown_runtime=shutdown_runtime,
        handle_callback_to_gateway_agent=lambda *_args: None,
    )

    assert handle.wait(timeout=1.0)
    logger.critical.assert_called_once_with(case.fatal_message, exc_info=True)
    shutdown_runtime.assert_called_once_with(runtime)
