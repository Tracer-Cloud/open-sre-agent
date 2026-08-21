"""Background Telegram gateway service."""

from __future__ import annotations

import asyncio
import logging
import threading

from config.constants.gateway import NO_ACTIVE_TURN_MESSAGE
from gateway.core.middleware.active_turns import is_stop_command
from gateway.core.process.polling_thread import PollingBackground, start_polling_background
from gateway.transports.telegram.approvals import handle_callback_query
from gateway.transports.telegram.inbound_handler import (
    handle_polled_inbound_telegram_message,
)
from gateway.transports.telegram.poller.poller import TelegramPoller
from gateway.transports.telegram.runtime import (
    InitializeTelegramPollingRuntime,
    ShutdownTelegramPollingRuntime,
    TelegramPollingRuntime,
)
from gateway.transports.telegram.settings import GatewaySettings
from infrastructure.turn_host.turn_callback import TurnCallback


def start_telegram_gateway_background(
    *,
    settings: GatewaySettings,
    logger: logging.Logger,
    initialize_runtime: InitializeTelegramPollingRuntime,
    shutdown_runtime: ShutdownTelegramPollingRuntime,
    handle_callback_to_gateway_agent: TurnCallback,
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
    handle_callback_to_gateway_agent: TurnCallback,
) -> None:
    """Poll Telegram updates and dispatch them until shutdown is requested."""
    poller = TelegramPoller(settings.bot_token)
    turn_semaphore = asyncio.Semaphore(settings.max_concurrent_turns)

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
                await handle_polled_inbound_telegram_message(
                    event,
                    client=resources.client,
                    session_resolver=resources.session_resolver,
                    settings=settings,
                    executor=resources.executor,
                    chat_locks=resources.chat_locks,
                    turn_semaphore=turn_semaphore,
                    approvals=resources.approvals,
                    active_cancels=resources.active_cancels,
                    loop=loop,
                    handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
                )

        except Exception:
            logger.error("Error while polling Telegram updates", exc_info=True)
            await asyncio.to_thread(stop_event.wait, 2)
