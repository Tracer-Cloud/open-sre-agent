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
        # Dispatch concurrently (matching the webhook path in gateway/app.py): an
        # approval-gated turn blocks until the inbound callback that approves it is
        # processed, so awaiting each event in turn would deadlock the poll loop
        # against itself. The set keeps task references alive (create_task alone
        # lets them be garbage-collected mid-flight).
        pending: set[asyncio.Task[None]] = set()
        try:
            while not stop_event.is_set():
                events = await asyncio.to_thread(poller.poll_once)
                for event in events:
                    task = asyncio.create_task(runner.handle_inbound(event))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                await asyncio.sleep(0)
        finally:
            # Drain in-flight turns before the loop stops. Once
            # run_until_complete returns, the loop is no longer running and any
            # task still awaiting an executor future can never resume — the
            # following runner.shutdown()/loop.close() would then tear down
            # resources mid-flight ("Task was destroyed but it is pending!").
            # Cancelling lets each task unwind on this still-running loop.
            for task in list(pending):
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

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
