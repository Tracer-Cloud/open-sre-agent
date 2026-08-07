"""Background Telegram gateway service."""

from __future__ import annotations

import asyncio
import logging
import threading

from gateway.core.runtime.active_turns import is_stop_command
from gateway.core.runtime.sink_protocol import GatewayAgentCallback
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


class TelegramGatewayBackground:
    """Control handle for the background Telegram gateway thread."""

    def __init__(
        self,
        *,
        thread: threading.Thread,
        stop_event: threading.Event,
    ) -> None:
        self._thread = thread
        self._stop_event = stop_event

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Request shutdown and return whether the thread stopped."""
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def wait(self, *, timeout: float | None = None) -> bool:
        """Wait for the thread and return whether it has stopped."""
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()


def start_telegram_gateway_background(
    *,
    settings: GatewaySettings,
    logger: logging.Logger,
    initialize_runtime: InitializeTelegramPollingRuntime,
    shutdown_runtime: ShutdownTelegramPollingRuntime,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
) -> TelegramGatewayBackground:
    """Start Telegram polling in a background thread."""
    stop_event = threading.Event()

    thread = threading.Thread(
        target=_run_telegram_gateway_thread,
        kwargs={
            "settings": settings,
            "stop_event": stop_event,
            "logger": logger,
            "initialize_runtime": initialize_runtime,
            "shutdown_runtime": shutdown_runtime,
            "handle_callback_to_gateway_agent": handle_callback_to_gateway_agent,
        },
        name="TelegramGatewayThread",
        daemon=True,
    )
    thread.start()

    logger.info("[telegram-gateway] polling started")
    return TelegramGatewayBackground(thread=thread, stop_event=stop_event)


def _run_telegram_gateway_thread(
    *,
    settings: GatewaySettings,
    stop_event: threading.Event,
    logger: logging.Logger,
    initialize_runtime: InitializeTelegramPollingRuntime,
    shutdown_runtime: ShutdownTelegramPollingRuntime,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
) -> None:
    """Own Telegram polling resources for the lifetime of the thread."""
    # Consideration: We could initialize a broader set of resources here that could be used by the gateway (i.e. the agent itself)
    resources = initialize_runtime(settings)

    try:
        asyncio.run(
            _poll_telegram_until_stopped(
                settings=settings,
                stop_event=stop_event,
                logger=logger,
                resources=resources,
                handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
            )
        )
    except Exception:
        logger.critical("Fatal error in Telegram gateway thread", exc_info=True)
    finally:
        shutdown_runtime(resources)


async def _poll_telegram_until_stopped(
    *,
    settings: GatewaySettings,
    stop_event: threading.Event,
    logger: logging.Logger,
    resources: TelegramPollingRuntime,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
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
                        resources.client.send_message(event.chat_id, "Nothing running to stop.")
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
