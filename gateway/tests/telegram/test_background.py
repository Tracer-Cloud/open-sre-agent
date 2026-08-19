from __future__ import annotations

import asyncio
import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.telegram import background
from gateway.transports.telegram.background import start_telegram_gateway_background
from gateway.transports.telegram.poller.poller import TelegramPollResult
from gateway.transports.telegram.runtime import (
    TelegramPollingRuntime,
    initialize_telegram_polling_runtime,
    shutdown_telegram_polling_runtime,
)
from gateway.transports.telegram.settings import GatewaySettings, TelegramInboundMessage

LOGGER = logging.getLogger("gateway.test")


@patch("gateway.transports.telegram.background.TelegramPoller")
def test_start_starts_poll_thread(mock_poller_cls: MagicMock) -> None:
    mock_poller_cls.return_value.poll_once.return_value = TelegramPollResult()
    handle = start_telegram_gateway_background(
        settings=GatewaySettings(bot_token="tok"),
        logger=LOGGER,
        initialize_runtime=initialize_telegram_polling_runtime,
        shutdown_runtime=shutdown_telegram_polling_runtime,
        handle_callback_to_gateway_agent=lambda *_args: None,
    )
    assert handle is not None
    handle.stop(timeout=1.0)
    mock_poller_cls.assert_called_once_with("tok")


def _message(text: str = "hello", *, chat_id: str = "chat-1") -> TelegramInboundMessage:
    return TelegramInboundMessage(
        update_id=1,
        user_id="user-1",
        chat_id=chat_id,
        message_id="msg-1",
        text=text,
    )


def _resources() -> TelegramPollingRuntime:
    return TelegramPollingRuntime(
        client=MagicMock(),
        bindings=MagicMock(),
        session_resolver=MagicMock(),
        chat_locks={},
        executor=MagicMock(),
        approvals=ApprovalBroker(),
        active_cancels=ActiveTurnRegistry(),
    )


class _FakePoller:
    """Serves scripted batches, then empty ones, counting every call."""

    def __init__(self, batches: list[TelegramPollResult]) -> None:
        self._batches = list(batches)
        self.calls = 0

    def poll_once(self) -> TelegramPollResult:
        self.calls += 1
        if self._batches:
            return self._batches.pop(0)
        # Real getUpdates long-polls; keep the idle loop off a hot spin.
        time.sleep(0.01)
        return TelegramPollResult()


def _run_loop(
    *,
    resources: TelegramPollingRuntime,
    stop_event: threading.Event,
) -> asyncio.Task[None]:
    return asyncio.create_task(
        background._poll_telegram_until_stopped(
            settings=GatewaySettings(bot_token="tok", shutdown_drain_seconds=2.0),
            stop_event=stop_event,
            logger=LOGGER,
            resources=resources,
            handle_callback_to_gateway_agent=lambda *_args: None,
        )
    )


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_polling_continues_while_a_turn_is_in_flight() -> None:
    """Regression: a turn awaiting approval must not freeze the poll loop.

    The click that resolves an approval only ever arrives on a *later*
    ``poll_once``. A loop that awaits the turn inline is still suspended on
    that turn, so it can never reach the poll that would deliver the click —
    the request always expires to denied no matter how fast the user clicks.
    """
    # Arrange
    poller = _FakePoller([TelegramPollResult(messages=[_message()])])
    turn_started = asyncio.Event()
    release_turn = asyncio.Event()

    async def _turn_blocked_on_approval(_event, **_kwargs) -> None:
        turn_started.set()
        await release_turn.wait()

    stop_event = threading.Event()

    with (
        patch.object(background, "TelegramPoller", lambda _token: poller),
        patch.object(
            background,
            "handle_polled_inbound_telegram_message",
            _turn_blocked_on_approval,
        ),
    ):
        loop_task = _run_loop(resources=_resources(), stop_event=stop_event)

        # Act
        await asyncio.wait_for(turn_started.wait(), timeout=2.0)
        polls_when_turn_began = poller.calls
        await _wait_until(lambda: poller.calls > polls_when_turn_began)

        # Assert — the loop polled again while the turn was still blocked.
        assert poller.calls > polls_when_turn_began
        assert not release_turn.is_set()  # the turn never unblocked itself

        stop_event.set()
        release_turn.set()
        await asyncio.wait_for(loop_task, timeout=2.0)


@pytest.mark.asyncio
async def test_a_failing_turn_does_not_stop_polling() -> None:
    """A detached turn's exception is logged, never left to kill the loop."""
    # Arrange
    poller = _FakePoller([TelegramPollResult(messages=[_message()])])

    async def _exploding_turn(_event, **_kwargs) -> None:
        msg = "turn blew up"
        raise RuntimeError(msg)

    stop_event = threading.Event()

    with (
        patch.object(background, "TelegramPoller", lambda _token: poller),
        patch.object(background, "handle_polled_inbound_telegram_message", _exploding_turn),
    ):
        loop_task = _run_loop(resources=_resources(), stop_event=stop_event)

        # Act
        await _wait_until(lambda: poller.calls >= 3)

        # Assert
        assert not loop_task.done()
        stop_event.set()
        await asyncio.wait_for(loop_task, timeout=2.0)
        assert loop_task.exception() is None


@pytest.mark.asyncio
async def test_in_flight_turns_finish_before_shutdown_returns() -> None:
    """Detached turns are drained, not cancelled mid-flight by ``asyncio.run``."""
    # Arrange
    poller = _FakePoller([TelegramPollResult(messages=[_message()])])
    turn_started = asyncio.Event()
    turn_finished = False

    async def _slow_turn(_event, **_kwargs) -> None:
        nonlocal turn_finished
        turn_started.set()
        await asyncio.sleep(0.05)
        turn_finished = True

    stop_event = threading.Event()

    with (
        patch.object(background, "TelegramPoller", lambda _token: poller),
        patch.object(background, "handle_polled_inbound_telegram_message", _slow_turn),
    ):
        loop_task = _run_loop(resources=_resources(), stop_event=stop_event)
        await asyncio.wait_for(turn_started.wait(), timeout=2.0)

        # Act — shut down while the turn is still running.
        stop_event.set()
        await asyncio.wait_for(loop_task, timeout=3.0)

    # Assert
    assert turn_finished is True


@pytest.mark.asyncio
async def test_stop_command_is_handled_without_dispatching_a_turn() -> None:
    """`/stop` still resolves through the registry, never as a new turn."""
    # Arrange
    poller = _FakePoller([TelegramPollResult(messages=[_message("/stop")])])
    dispatched = False

    async def _turn(_event, **_kwargs) -> None:
        nonlocal dispatched
        dispatched = True

    resources = _resources()
    stop_event = threading.Event()

    with (
        patch.object(background, "TelegramPoller", lambda _token: poller),
        patch.object(background, "handle_polled_inbound_telegram_message", _turn),
    ):
        loop_task = _run_loop(resources=resources, stop_event=stop_event)

        # Act
        await _wait_until(lambda: poller.calls >= 2)
        stop_event.set()
        await asyncio.wait_for(loop_task, timeout=2.0)

    # Assert
    assert dispatched is False
    resources.client.send_message.assert_called_once()
