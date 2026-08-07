"""Background Buzz gateway service."""

from __future__ import annotations

import asyncio
import logging
import threading

from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.transports.buzz.inbound_handler import handle_polled_inbound_buzz_message
from gateway.transports.buzz.poller.poller import BuzzFeedPoller
from gateway.transports.buzz.runtime import (
    BuzzPollingRuntime,
    InitializeBuzzPollingRuntime,
    ShutdownBuzzPollingRuntime,
)
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings

logger = logging.getLogger(__name__)


class BuzzGatewayBackground:
    """Control handle for the background Buzz gateway thread."""

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


def start_buzz_gateway_background(
    *,
    settings: GatewaySettings,
    logger: logging.Logger,
    initialize_runtime: InitializeBuzzPollingRuntime,
    shutdown_runtime: ShutdownBuzzPollingRuntime,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
) -> BuzzGatewayBackground:
    """Start Buzz mention polling in a background thread."""
    stop_event = threading.Event()

    thread = threading.Thread(
        target=_run_buzz_gateway_thread,
        kwargs={
            "settings": settings,
            "stop_event": stop_event,
            "logger": logger,
            "initialize_runtime": initialize_runtime,
            "shutdown_runtime": shutdown_runtime,
            "handle_callback_to_gateway_agent": handle_callback_to_gateway_agent,
        },
        name="BuzzGatewayThread",
        daemon=True,
    )
    thread.start()

    logger.info("[buzz-gateway] polling started")
    return BuzzGatewayBackground(thread=thread, stop_event=stop_event)


def _run_buzz_gateway_thread(
    *,
    settings: GatewaySettings,
    stop_event: threading.Event,
    logger: logging.Logger,
    initialize_runtime: InitializeBuzzPollingRuntime,
    shutdown_runtime: ShutdownBuzzPollingRuntime,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
) -> None:
    """Own Buzz polling resources for the lifetime of the thread."""
    resources = initialize_runtime(settings)

    try:
        asyncio.run(
            _poll_buzz_until_stopped(
                settings=settings,
                stop_event=stop_event,
                logger=logger,
                resources=resources,
                handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
            )
        )
    except Exception:
        logger.critical("Fatal error in Buzz gateway thread", exc_info=True)
    finally:
        shutdown_runtime(resources)


async def _poll_buzz_until_stopped(
    *,
    settings: GatewaySettings,
    stop_event: threading.Event,
    logger: logging.Logger,
    resources: BuzzPollingRuntime,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
) -> None:
    """Poll the mention feed and dispatch (or resolve approvals) until shutdown.

    ``feed get`` is a single non-blocking HTTP call, unlike Telegram's
    long-poll ``getUpdates`` — so this loop sleeps for the configured
    interval itself between polls, waking early only on shutdown.
    """
    poller = BuzzFeedPoller(resources.client)
    turn_semaphore = asyncio.Semaphore(settings.max_concurrent_turns)

    while not stop_event.is_set():
        try:
            events = await asyncio.to_thread(poller.poll_once)
            loop = asyncio.get_running_loop()

            for event in events:
                if _resolve_if_approval_reply(event, resources):
                    continue
                await handle_polled_inbound_buzz_message(
                    event,
                    client=resources.client,
                    session_resolver=resources.session_resolver,
                    settings=settings,
                    executor=resources.executor,
                    chat_locks=resources.chat_locks,
                    turn_semaphore=turn_semaphore,
                    approvals=resources.approvals,
                    pending_approvals=resources.pending_approvals,
                    loop=loop,
                    handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
                )

        except Exception:
            logger.error("Error while polling Buzz mentions", exc_info=True)

        await asyncio.to_thread(stop_event.wait, settings.poll_interval_seconds)


def _resolve_if_approval_reply(event: BuzzInboundMessage, resources: BuzzPollingRuntime) -> bool:
    """Resolve *event* against a pending approval if its reply targets one.

    Runs on the poll loop's own thread — never the turn executor, so a
    waiting turn's ``ApprovalBroker.wait`` never contends with the same
    lock/semaphore it is blocked on.
    """
    approval_id = resources.pending_approvals.pop_match(event.reply_event_ids)
    if approval_id is None:
        return False
    resources.approvals.resolve(
        approval_id, approved=_is_approve(event.content), decided_by=event.pubkey
    )
    return True


def _is_approve(text: str) -> bool:
    first_word = text.strip().split(maxsplit=1)
    return bool(first_word) and first_word[0].lower().startswith(("approve", "yes"))


__all__ = ["BuzzGatewayBackground", "start_buzz_gateway_background"]
