"""Background Telegram gateway service."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Final

from config.constants.gateway import NO_ACTIVE_TURN_MESSAGE
from gateway.core.middleware.active_turns import ActiveTurnRegistry, is_stop_command
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.process.polling_thread import PollingBackground, start_polling_background
from gateway.core.storage import SessionResolver
from gateway.core.transport_api import GatewayAgentCallback
from gateway.transports.telegram.approvals import handle_callback_query
from gateway.transports.telegram.inbound_handler import (
    handle_polled_inbound_telegram_message,
)
from gateway.transports.telegram.poller.client import TelegramBotClient
from gateway.transports.telegram.poller.poller import TelegramPoller
from gateway.transports.telegram.runtime import (
    InitializeTelegramPollingRuntime,
    ShutdownTelegramPollingRuntime,
    TelegramPollingRuntime,
)
from gateway.transports.telegram.settings import GatewaySettings, TelegramInboundMessage

# Bound on waiting for in-flight turn tasks to finish once polling stops, so
# shutdown stays prompt even if a turn is still parked in an approval wait
# that can no longer be resolved (nothing polls for the click any more).
_SHUTDOWN_DRAIN_SECONDS: Final = 5.0


def start_telegram_gateway_background(
    *,
    settings: GatewaySettings,
    logger: logging.Logger,
    initialize_runtime: InitializeTelegramPollingRuntime,
    shutdown_runtime: ShutdownTelegramPollingRuntime,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
) -> PollingBackground:
    """Start Telegram polling in a background thread."""

    def _initialize_runtime() -> TelegramPollingRuntime:
        return initialize_runtime(settings)

    async def _poll_until_stopped(
        resources: TelegramPollingRuntime,
        stop_event: threading.Event,
    ) -> None:
        await _poll_telegram_until_stopped(
            settings=settings,
            stop_event=stop_event,
            logger=logger,
            resources=resources,
            handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
        )

    return start_polling_background(
        thread_name="TelegramGatewayThread",
        started_message="[telegram-gateway] polling started",
        fatal_message="Fatal error in Telegram gateway thread",
        logger=logger,
        initialize_runtime=_initialize_runtime,
        poll_until_stopped=_poll_until_stopped,
        shutdown_runtime=shutdown_runtime,
    )


async def _poll_telegram_until_stopped(
    *,
    settings: GatewaySettings,
    stop_event: threading.Event,
    logger: logging.Logger,
    resources: TelegramPollingRuntime,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
) -> None:
    """Poll Telegram updates and dispatch them until shutdown is requested.

    Turn dispatch is fired as a background task, never awaited inline: a turn
    can sit blocked in ``ApprovalBroker.wait`` for up to
    ``MAX_APPROVAL_WAIT_SECONDS``, and this same loop is the only thing that
    can ever deliver the callback_query click that unblocks it. Awaiting the
    turn here would stall polling for the duration of every approval wait, so
    the click that resolves it could never arrive (see the Buzz transport's
    ``_poll_buzz_until_stopped`` for the same reasoning).
    """
    poller = TelegramPoller(settings.bot_token)
    turn_semaphore = asyncio.Semaphore(settings.max_concurrent_turns)
    pending_tasks: set[asyncio.Task[None]] = set()

    resources.client.delete_webhook()

    while not stop_event.is_set():
        try:
            batch = await asyncio.to_thread(poller.poll_once)
            loop = asyncio.get_running_loop()

            for callback in batch.callbacks:
                handle_callback_query(
                    callback,
                    broker=resources.approvals,
                    client=resources.client,
                    allowed_user_ids=settings.allowed_user_ids,
                )

            for event in batch.messages:
                # /stop must not wait on the per-user turn lock — resolve via
                # the active-turn registry before dispatching a new turn.
                if is_stop_command(event.text):
                    if not resources.active_cancels.request_stop(event.chat_id):
                        resources.client.send_message(event.chat_id, NO_ACTIVE_TURN_MESSAGE)
                    continue
                # Registered before the task exists: a /stop later in this
                # same batch must find the accepted turn, not "nothing".
                turn_cancel = threading.Event()
                resources.active_cancels.register(event.chat_id, turn_cancel)
                task = asyncio.create_task(
                    _dispatch_turn(
                        event,
                        client=resources.client,
                        session_resolver=resources.session_resolver,
                        settings=settings,
                        executor=resources.executor,
                        chat_locks=resources.chat_locks,
                        turn_semaphore=turn_semaphore,
                        approvals=resources.approvals,
                        active_cancels=resources.active_cancels,
                        turn_cancel=turn_cancel,
                        loop=loop,
                        handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
                        logger=logger,
                    )
                )
                pending_tasks.add(task)
                task.add_done_callback(pending_tasks.discard)

        except Exception:
            logger.error("Error while polling Telegram updates", exc_info=True)
            await asyncio.to_thread(stop_event.wait, 2)

    await _drain_pending_turns(pending_tasks, approvals=resources.approvals, logger=logger)


async def _dispatch_turn(
    event: TelegramInboundMessage,
    *,
    client: TelegramBotClient,
    session_resolver: SessionResolver,
    settings: GatewaySettings,
    executor: ThreadPoolExecutor,
    chat_locks: dict[str, asyncio.Lock],
    turn_semaphore: asyncio.Semaphore,
    approvals: ApprovalBroker,
    active_cancels: ActiveTurnRegistry,
    turn_cancel: threading.Event,
    loop: asyncio.AbstractEventLoop,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
    logger: logging.Logger,
) -> None:
    """Run one turn as a detached task; a failure here must not kill polling."""
    try:
        await handle_polled_inbound_telegram_message(
            event,
            client=client,
            session_resolver=session_resolver,
            settings=settings,
            executor=executor,
            chat_locks=chat_locks,
            turn_semaphore=turn_semaphore,
            approvals=approvals,
            active_cancels=active_cancels,
            turn_cancel=turn_cancel,
            loop=loop,
            handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
        )
    except Exception:
        logger.error(
            "[telegram-gateway] turn dispatch failed chat=%s",
            event.chat_id,
            exc_info=True,
        )
    finally:
        active_cancels.unregister(event.chat_id, turn_cancel)


async def _drain_pending_turns(
    pending_tasks: set[asyncio.Task[None]],
    *,
    approvals: ApprovalBroker,
    logger: logging.Logger,
) -> None:
    """Let in-flight turn tasks finish before the event loop closes under them.

    Polling has stopped, so nothing can deliver an approval click any more —
    a turn parked in ``ApprovalBroker.wait`` would otherwise sit until its own
    (up to 180s) expiry, and cancelling its asyncio Task does not stop the
    blocking wait on the executor thread underneath it. Denying every pending
    approval first lets those threads return immediately, so the bounded task
    wait below (and the executor shutdown that follows it) do not inherit
    that expiry. The wait is bounded rather than generous; anything still
    running past the bound is cancelled so shutdown stays prompt.
    """
    denied = approvals.drain()
    if denied:
        logger.info(
            "[telegram-gateway] denied %d pending approval(s) at shutdown",
            len(denied),
        )
    if not pending_tasks:
        return
    _done, still_running = await asyncio.wait(pending_tasks, timeout=_SHUTDOWN_DRAIN_SECONDS)
    for task in still_running:
        task.cancel()
    if still_running:
        logger.warning(
            "[telegram-gateway] shutting down with %d unfinished turn(s)",
            len(still_running),
        )
