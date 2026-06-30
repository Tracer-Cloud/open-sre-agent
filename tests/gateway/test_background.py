"""Tests for concurrent poll-mode dispatch in the Telegram gateway background."""

from __future__ import annotations

import asyncio
import logging
import threading
from unittest.mock import MagicMock, patch

from gateway.config.get_gateway_settings import GatewaySettings
from gateway.polling.telegram_gateway_background import _poll_telegram_until_stopped
from gateway.polling.telegram_polling_runtime import TelegramPollingRuntime


def _make_resources() -> TelegramPollingRuntime:
    client = MagicMock()
    client.delete_webhook.return_value = None
    return TelegramPollingRuntime(
        client=client,
        db=MagicMock(),
        session_resolver=MagicMock(),
        chat_locks={},
        executor=MagicMock(),
    )


class _FakePoller:
    """Returns a message on poll 1, a callback on poll 2, then signals stop."""

    def __init__(self, stop_event: threading.Event) -> None:
        self._stop_event = stop_event
        self._calls = 0

    def poll_once(self) -> list[str]:
        self._calls += 1
        if self._calls == 1:
            return ["message"]
        if self._calls == 2:
            return ["callback"]
        if self._calls >= 4:
            self._stop_event.set()
        return []


def test_poll_loop_dispatches_events_concurrently() -> None:
    """An approval-gated turn must not block fetching the callback that approves it.

    Mirrors the real deadlock shape: the message turn waits for a blocker event
    that is only set when the callback is dispatched. Sequential await would
    deadlock; concurrent create_task lets both proceed.
    """
    stop_event = threading.Event()
    order: list[str] = []
    blocker: asyncio.Event | None = None

    async def fake_handle(event: str, **_kwargs: object) -> None:
        nonlocal blocker
        if event == "message":
            if blocker is None:
                blocker = asyncio.Event()
            order.append("message_start")
            await blocker.wait()
            order.append("message_end")
        else:
            order.append("callback")
            if blocker is not None:
                blocker.set()

    resources = _make_resources()

    with (
        patch(
            "gateway.polling.telegram_gateway_background.TelegramPoller",
            side_effect=lambda _token: _FakePoller(stop_event),
        ),
        patch(
            "gateway.polling.telegram_gateway_background.handle_polled_inbound_telegram_message",
            side_effect=fake_handle,
        ),
    ):
        asyncio.run(
            _poll_telegram_until_stopped(
                settings=GatewaySettings(bot_token="tok"),
                stop_event=stop_event,
                logger=logging.getLogger("gateway.test"),
                resources=resources,
            )
        )

    assert "callback" in order, "callback event was never dispatched (deadlock)"
    assert "message_end" in order, "message turn never completed"


def test_poll_loop_drains_in_flight_tasks_on_shutdown() -> None:
    """Tasks in flight when stop_event fires must be cancelled, not abandoned."""
    stop_event = threading.Event()
    started: list[str] = []
    cancelled: list[str] = []

    async def slow_handle(event: str, **_kwargs: object) -> None:
        started.append(event)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(event)
            raise

    call_count = 0

    def fake_poll_once() -> list[str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ["msg"]
        stop_event.set()
        return []

    resources = _make_resources()

    with (
        patch(
            "gateway.polling.telegram_gateway_background.TelegramPoller",
            return_value=MagicMock(poll_once=fake_poll_once),
        ),
        patch(
            "gateway.polling.telegram_gateway_background.handle_polled_inbound_telegram_message",
            side_effect=slow_handle,
        ),
    ):
        asyncio.run(
            _poll_telegram_until_stopped(
                settings=GatewaySettings(bot_token="tok"),
                stop_event=stop_event,
                logger=logging.getLogger("gateway.test"),
                resources=resources,
            )
        )

    assert "msg" in started, "slow turn was never started"
    assert "msg" in cancelled, "in-flight task was not cancelled on shutdown"
