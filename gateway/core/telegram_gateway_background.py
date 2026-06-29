"""Background Telegram gateway service."""

from __future__ import annotations

import asyncio
import logging
import threading

from gateway.config.get_gateway_settings import GatewaySettings
from gateway.core.gateway_runner import GatewayRunner
from gateway.platforms.telegram.poller import TelegramPoller


class TelegramGatewayBackground:
    """Control handle for the background gateway thread."""

    def __init__(self, *, thread: threading.Thread, stop_event: threading.Event) -> None:
        self._thread = thread
        self._stop_event = stop_event

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Request shutdown and wait for thread termination."""
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def wait(self, *, timeout: float | None = None) -> None:
        self._thread.join(timeout=timeout)


def run_telegram_polling_loop(
    settings: GatewaySettings,
    stop_event: threading.Event,
    logger: logging.Logger,
) -> None:
    """Main polling loop – must be extremely resilient."""
    runner = GatewayRunner(settings)

    # Initialize the daemonized event loop for the Telegram polling
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        runner.bind_loop(loop)
        runner.clear_webhook()

        initialized_telegram_poller = TelegramPoller(settings.bot_token)

        async def process_inbound_telegram_events() -> None:
            while not stop_event.is_set():
                try:
                    inbound_telegram_events = await asyncio.to_thread(
                        initialized_telegram_poller.poll_once
                    )
                    for event in inbound_telegram_events:
                        await runner.handle_inbound(event)
                    await asyncio.sleep(0)
                except Exception:
                    logger.error("Error in Telegram polling loop", exc_info=True)
                    await asyncio.sleep(2)

        loop.run_until_complete(process_inbound_telegram_events())

    except Exception:
        logger.critical("Fatal error in gateway thread", exc_info=True)
    finally:
        try:
            runner.shutdown()
        except Exception:
            logger.error("Error during runner shutdown", exc_info=True)
        try:
            loop.close()
        except Exception:
            pass


def start_telegram_gateway_background(
    *,
    settings: GatewaySettings,
    logger: logging.Logger,
) -> TelegramGatewayBackground:
    """Start the Telegram gateway polling loop in a background thread."""
    stop_event = threading.Event()

    thread = threading.Thread(
        target=run_telegram_polling_loop,
        args=(settings, stop_event, logger),
        name="TelegramGateway-PollingLoop-v1",
        daemon=True,
    )
    thread.start()

    logger.info("[telegram-gateway] poll mode started successfully")
    return TelegramGatewayBackground(thread=thread, stop_event=stop_event)
