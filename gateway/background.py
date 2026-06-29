"""Background Telegram gateway service for co-located ``opensre`` REPL runs."""

from __future__ import annotations

import asyncio
import logging
import os
import threading

from dotenv import load_dotenv

from gateway.config import GatewaySettings, load_gateway_settings
from gateway.platforms.telegram.poller import TelegramPoller
from gateway.runner import GatewayRunner

logger = logging.getLogger(__name__)


def telegram_gateway_auto_start_enabled() -> bool:
    """Return whether the REPL should start the Telegram gateway automatically."""
    raw = os.environ.get("TELEGRAM_GATEWAY_AUTO_START", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _poll_mode(settings: GatewaySettings) -> bool:
    return not settings.webhook_url.strip()


class TelegramGatewayBackground:
    """Daemon thread running the Telegram gateway poll loop."""

    def __init__(self, *, thread: threading.Thread, stop_event: threading.Event) -> None:
        self._thread = thread
        self._stop_event = stop_event

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout=timeout)


def _configure_co_located_gateway_logging() -> None:
    """Keep co-located gateway diagnostics off the interactive REPL terminal."""
    gateway_logger = logging.getLogger("gateway")
    if gateway_logger.handlers:
        return
    gateway_logger.addHandler(logging.NullHandler())
    gateway_logger.propagate = False


def run_poll_loop(settings: GatewaySettings, stop_event: threading.Event) -> None:
    """Run the Telegram long-poll loop until ``stop_event`` is set."""
    runner = GatewayRunner(settings)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner.bind_loop(loop)
    runner.clear_webhook()
    poller = TelegramPoller(settings.bot_token)

    async def _loop_body() -> None:
        while not stop_event.is_set():
            events = await asyncio.to_thread(poller.poll_once)
            for event in events:
                await runner.handle_inbound(event)
            await asyncio.sleep(0)

    try:
        loop.run_until_complete(_loop_body())
    finally:
        runner.shutdown()
        loop.close()


def try_start_telegram_gateway_background() -> TelegramGatewayBackground | None:
    """Start poll-mode Telegram gateway on a daemon thread when configured.

    Webhook mode is intentionally excluded: production webhook deployments should
    run ``opensre gateway telegram`` as a dedicated process.
    """
    load_dotenv(override=False)
    if not telegram_gateway_auto_start_enabled():
        return None
    try:
        settings = load_gateway_settings()
    except ValueError as exc:
        logger.debug("[telegram-gateway] auto-start skipped: %s", exc)
        return None
    if not settings.bot_token:
        return None
    if not _poll_mode(settings):
        logger.debug(
            "[telegram-gateway] auto-start skipped: TELEGRAM_WEBHOOK_URL is set "
            "(run `opensre gateway telegram` separately)"
        )
        return None

    stop_event = threading.Event()

    def _target() -> None:
        _configure_co_located_gateway_logging()
        run_poll_loop(settings, stop_event)

    thread = threading.Thread(
        target=_target,
        name="telegram-gateway",
        daemon=True,
    )
    thread.start()
    logger.debug("[telegram-gateway] auto-start poll mode active")
    return TelegramGatewayBackground(thread=thread, stop_event=stop_event)


__all__ = [
    "TelegramGatewayBackground",
    "run_poll_loop",
    "telegram_gateway_auto_start_enabled",
    "try_start_telegram_gateway_background",
]
