from __future__ import annotations

import asyncio
import logging
import threading
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
from gateway.transports.telegram.settings import (
    GatewaySettings,
    TelegramCallbackQuery,
    TelegramInboundMessage,
)


@patch("gateway.transports.telegram.background.TelegramPoller")
def test_start_starts_poll_thread(mock_poller_cls: MagicMock) -> None:
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


def test_poll_loop_delivers_callback_while_a_turn_is_still_dispatching() -> None:
    """Regression for #5076: turn dispatch must not block the next ``poll_once``.

    Before the fix, ``handle_polled_inbound_telegram_message`` was awaited
    inline in the poll loop, so a turn blocked in an approval wait could
    never let the loop reach the next ``poll_once`` call that would deliver
    the callback_query click resolving it. This drives the loop through a
    message batch that starts a (deliberately hanging) turn, then a second
    batch carrying the approval callback, and asserts the callback is
    observed and handled while the first turn is still in flight — proving
    the loop is not stuck awaiting it.

    Under the pre-fix inline-await code, this test times out: the second
    ``poll_once`` is never reached.
    """
    resources = _resources()
    stop_event = threading.Event()
    turn_started = asyncio.Event()
    release_turn = asyncio.Event()
    callback_delivered = asyncio.Event()
    handled_callbacks: list[TelegramCallbackQuery] = []

    async def _hanging_dispatch(_event: object, **_kw: object) -> None:
        turn_started.set()
        await release_turn.wait()

    def _handle_callback_query(callback: TelegramCallbackQuery, **_kw: object) -> bool:
        handled_callbacks.append(callback)
        callback_delivered.set()
        return True

    message = TelegramInboundMessage(
        update_id=1, user_id="u1", chat_id="c1", message_id="m1", text="do the thing"
    )
    callback = TelegramCallbackQuery(
        update_id=2, user_id="u1", chat_id="c1", callback_query_id="cb1", data="approve:aid"
    )
    call_count = 0

    def _poll_once() -> TelegramPollResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return TelegramPollResult(messages=[message])
        if call_count == 2:
            return TelegramPollResult(callbacks=[callback])
        stop_event.set()
        return TelegramPollResult()

    poller = MagicMock()
    poller.poll_once.side_effect = _poll_once

    async def _run() -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(background, "TelegramPoller", lambda _token: poller)
            mp.setattr(background, "handle_polled_inbound_telegram_message", _hanging_dispatch)
            mp.setattr(background, "handle_callback_query", _handle_callback_query)

            task = asyncio.create_task(
                background._poll_telegram_until_stopped(
                    settings=GatewaySettings(bot_token="tok"),
                    stop_event=stop_event,
                    logger=logging.getLogger("gateway.test"),
                    resources=resources,
                    handle_callback_to_gateway_agent=lambda *_a: None,
                )
            )
            await asyncio.wait_for(turn_started.wait(), timeout=1)
            await asyncio.wait_for(callback_delivered.wait(), timeout=1)
            release_turn.set()
            await asyncio.wait_for(task, timeout=1)

    asyncio.run(_run())

    assert handled_callbacks == [callback]


def test_stop_in_same_batch_cancels_the_just_dispatched_turn() -> None:
    """Regression: ``/stop`` in the same poll batch as its target message must
    find the just-dispatched turn instead of reporting "no active turn".

    Dispatch-time registration in ``ActiveTurnRegistry`` happens in the poll
    loop itself, before ``create_task`` — so a same-batch ``/stop`` can find
    and cancel a turn whose task has not even started running yet.
    """
    resources = _resources()
    stop_event = threading.Event()
    dispatch_started = asyncio.Event()
    seen_cancel_events: list[threading.Event] = []

    async def _hanging_dispatch(
        _event: object, *, turn_cancel: threading.Event, **_kw: object
    ) -> None:
        seen_cancel_events.append(turn_cancel)
        dispatch_started.set()
        await asyncio.Event().wait()

    message = TelegramInboundMessage(
        update_id=1, user_id="u1", chat_id="c1", message_id="m1", text="do the thing"
    )
    stop_message = TelegramInboundMessage(
        update_id=2, user_id="u1", chat_id="c1", message_id="m2", text="/stop"
    )
    call_count = 0

    def _poll_once() -> TelegramPollResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return TelegramPollResult(messages=[message, stop_message])
        stop_event.set()
        return TelegramPollResult()

    poller = MagicMock()
    poller.poll_once.side_effect = _poll_once

    async def _run() -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(background, "TelegramPoller", lambda _token: poller)
            mp.setattr(background, "handle_polled_inbound_telegram_message", _hanging_dispatch)

            task = asyncio.create_task(
                background._poll_telegram_until_stopped(
                    settings=GatewaySettings(bot_token="tok"),
                    stop_event=stop_event,
                    logger=logging.getLogger("gateway.test"),
                    resources=resources,
                    handle_callback_to_gateway_agent=lambda *_a: None,
                )
            )
            await asyncio.wait_for(dispatch_started.wait(), timeout=1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())

    resources.client.send_message.assert_not_called()  # no "no active turn" reply
    assert len(seen_cancel_events) == 1
    assert seen_cancel_events[0].is_set()


def test_shutdown_denies_pending_approvals_before_draining() -> None:
    """A turn parked in ``ApprovalBroker.wait`` must not outlive the drain budget."""
    resources = _resources()
    approval_id = resources.approvals.create(platform="telegram", chat_id="c1")
    waited: list[tuple[bool, str]] = []

    async def _run() -> None:
        waiter = asyncio.get_running_loop().run_in_executor(
            None, lambda: resources.approvals.wait(approval_id, timeout=30)
        )
        await background._drain_pending_turns(
            set(), approvals=resources.approvals, logger=logging.getLogger("gateway.test")
        )
        waited.append(await asyncio.wait_for(waiter, timeout=1))

    asyncio.run(_run())

    assert waited == [(False, "")]


def test_approval_created_after_drain_resolves_immediately() -> None:
    """Regression: an approval requested after ``drain()`` must not open a
    fresh wait nothing can ever resolve.

    A turn that reaches its first approval request just after shutdown's
    one-time ``drain()`` swept the broker would otherwise create a normal
    pending entry and block ``wait()`` for up to its own expiry — which then
    blocks ``executor.shutdown(wait=True)`` for the same duration, since
    cancelling the wrapping asyncio Task does not stop that executor thread.
    """
    resources = _resources()

    async def _run() -> tuple[bool, str]:
        await background._drain_pending_turns(
            set(), approvals=resources.approvals, logger=logging.getLogger("gateway.test")
        )

        def _create_and_wait() -> tuple[bool, str]:
            approval_id = resources.approvals.create(platform="telegram", chat_id="c1")
            return resources.approvals.wait(approval_id, timeout=30)

        waiter = asyncio.get_running_loop().run_in_executor(None, _create_and_wait)
        return await asyncio.wait_for(waiter, timeout=1)

    result = asyncio.run(_run())

    assert result == (False, "")
