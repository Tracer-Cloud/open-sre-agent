"""Background Discord gateway lifecycle handle."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from gateway.discord.settings import DiscordGatewaySettings
from gateway.discord.worker import run_discord_gateway_thread
from gateway.runtime.sink_protocol import GatewayAgentCallback
from gateway.storage.session.binding_store import BindingStore, open_binding_store


class DiscordGatewayBackground:
    """Control handle for the background Discord gateway worker."""

    def __init__(
        self,
        *,
        thread: threading.Thread,
        stop_event: threading.Event,
        ready_event: threading.Event,
        bindings: BindingStore,
        executor: ThreadPoolExecutor,
    ) -> None:
        self._thread = thread
        self._stop_event = stop_event
        self._ready_event = ready_event
        self._bindings = bindings
        self._executor = executor

    def stop(self, *, timeout: float = 8.0) -> bool:
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        self._executor.shutdown(wait=False, cancel_futures=False)
        try:
            self._bindings.close()
        except Exception:
            logging.getLogger(__name__).debug(
                "[discord-gateway] binding store close failed", exc_info=True
            )
        return not self._thread.is_alive()

    def wait_until_ready(self, *, timeout: float) -> bool:
        return self._ready_event.wait(timeout)


def start_discord_gateway_background(
    *,
    settings: DiscordGatewaySettings,
    logger: logging.Logger,
    handler: GatewayAgentCallback,
) -> DiscordGatewayBackground:
    """Connect to Discord and dispatch inbound messages until stopped."""
    bindings = open_binding_store()
    executor = ThreadPoolExecutor(
        max_workers=settings.max_concurrent_turns,
        thread_name_prefix="DiscordGatewayTurn",
    )
    stop_event = threading.Event()
    ready_event = threading.Event()
    thread = threading.Thread(
        target=run_discord_gateway_thread,
        kwargs={
            "settings": settings,
            "logger": logger,
            "handler": handler,
            "bindings": bindings,
            "executor": executor,
            "stop_event": stop_event,
            "ready_event": ready_event,
        },
        name="DiscordGatewayThread",
        daemon=True,
    )
    thread.start()
    return DiscordGatewayBackground(
        thread=thread,
        stop_event=stop_event,
        ready_event=ready_event,
        bindings=bindings,
        executor=executor,
    )


__all__ = [
    "DiscordGatewayBackground",
    "start_discord_gateway_background",
]
