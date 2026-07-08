"""Gateway process entrypoint and lifecycle owner.

``GatewayManager`` is the composition root: it assembles the transport-agnostic
turn handler from a booted session's tools, starts the Telegram worker (whose
wiring lives in :mod:`gateway.telegram_gateway`), and owns the process lifecycle
(signals, ``stop``/``wait``). It holds no Telegram or agent-dispatch logic
itself — those live in :mod:`gateway.turn_handler` and
:mod:`gateway.telegram_gateway`.
"""

from __future__ import annotations

import logging
import signal

from rich.console import Console

from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.llm.internal.preload import preload_llm_clients
from gateway.config.configure_gateway_logging import configure_gateway_logging
from gateway.config.get_gateway_settings import GatewaySettings
from gateway.polling.telegram_gateway_background import TelegramGatewayBackground
from gateway.telegram_gateway import start_telegram_worker
from gateway.turn_handler import GatewayTurnHandler


class GatewayManager:
    """Composition root and lifecycle handle for the running gateway process."""

    def __init__(self) -> None:
        self.settings: GatewaySettings | None = None
        self.logger: logging.Logger | None = None
        self.telegram_background_worker: TelegramGatewayBackground | None = None

    def start_gateway(self, *, wait: bool = True) -> GatewayManager:
        """Assemble the turn handler, start the worker, and own its lifecycle."""
        from integrations.harness_adapters import register_harness_adapters as register_integrations
        from tools.harness_adapters import register_harness_adapters as register_tools

        harness = AgentHarness(HarnessConfig(open_storage=False))
        harness.resolve_env_variables()
        # Mirror the interactive shell boot path: register harness tool/integration
        # adapters so action tools (including slash_invoke) are available on gateway turns.
        register_integrations()
        register_tools()
        logger = configure_gateway_logging()

        # Load the LLM client graph as one snapshot at boot (avoids a stale
        # mixed-version process after a code change).
        preload_llm_clients()

        # Compose the transport-agnostic turn handler. Action tools are resolved
        # per turn from each chat's live session inside the handler (not here).
        console = Console(force_terminal=False)
        handler = GatewayTurnHandler(console=console)

        worker, settings = start_telegram_worker(logger=logger, handler=handler)

        def _stop(*_args: object) -> None:
            worker.stop()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        self.settings = settings
        self.logger = logger
        self.telegram_background_worker = worker

        if wait:
            self.wait()
        return self

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Request shutdown and return whether the background worker stopped."""
        if self.telegram_background_worker is None:
            return True
        return self.telegram_background_worker.stop(timeout=timeout)

    def wait(self, *, timeout: float | None = None) -> bool:
        """Wait for the gateway worker and return whether it has stopped."""
        if self.telegram_background_worker is None:
            return True
        return self.telegram_background_worker.wait(timeout=timeout)


def start_gateway(*, wait: bool = True) -> GatewayManager:
    """Compatibility wrapper for existing CLI/import callers."""
    return GatewayManager().start_gateway(wait=wait)


def main() -> None:
    GatewayManager().start_gateway()


if __name__ == "__main__":
    main()
