"""Gateway process entrypoint and lifecycle owner.

``GatewayManager`` is the composition root for the OpenSRE background agent:
it assembles the transport-agnostic turn handler from a booted session's tools
and starts every daemon component — the web health app, the Telegram chat
worker (when configured), and the scheduled-task runner — then owns the
process lifecycle (signals, ``stop``/``wait``). Component states are published
through :func:`gateway.daemon.write_component_status` so the CLI and the
interactive shell can report status. It holds no Telegram or agent-dispatch
logic itself — those live in :mod:`gateway.turn_handler` and
:mod:`gateway.telegram_gateway`.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from typing import Any

from rich.console import Console

from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.llm.internal.preload import preload_llm_clients
from gateway.config.configure_gateway_logging import configure_gateway_logging
from gateway.config.get_gateway_settings import GatewayConfigurationError, GatewaySettings
from gateway.daemon import (
    GATEWAY_PID_FILE,
    clear_component_status,
    write_component_status,
)
from gateway.polling.telegram_gateway_background import TelegramGatewayBackground
from gateway.telegram_gateway import start_telegram_worker
from gateway.turn_handler import GatewayTurnHandler


class GatewayManager:
    """Composition root and lifecycle handle for the running gateway process."""

    def __init__(self) -> None:
        self.settings: GatewaySettings | None = None
        self.logger: logging.Logger | None = None
        self.telegram_background_worker: TelegramGatewayBackground | None = None
        self.web_server: Any = None
        self.scheduler: Any = None
        self.components: dict[str, str] = {}
        self._stopped = threading.Event()

    def start_gateway(self, *, wait: bool = True) -> GatewayManager:
        """Assemble the turn handler, start all components, and own the lifecycle."""
        from integrations.harness_adapters import register_harness_adapters as register_integrations
        from tools.harness_adapters import register_harness_adapters as register_tools

        harness = AgentHarness(HarnessConfig(open_storage=False))
        harness.resolve_env_variables()
        # Mirror shell boot: register harness adapters here (gateway cannot import
        # surfaces.boundary without a surfaces↔gateway peer import).
        register_integrations()
        register_tools()
        # Env-gated (OPENSRE_NO_TELEMETRY / DO_NOT_TRACK / missing DSN) — free when off.
        from platform.observability.errors.sentry import init_sentry

        init_sentry(entrypoint="gateway")
        logger = self.logger = configure_gateway_logging()

        # Load the LLM client graph as one snapshot at boot (avoids a stale
        # mixed-version process after a code change).
        preload_llm_clients()

        # Compose the transport-agnostic turn handler. Action tools are resolved
        # per turn from each chat's live session inside the handler (not here).
        console = Console(force_terminal=False)
        handler = GatewayTurnHandler(console=console)

        self._start_web(logger)
        self._start_telegram(logger, handler)
        self._start_scheduler(logger)
        self._publish_status(logger)

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        if wait:
            self.wait()
        return self

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Shut down all components and return whether the chat worker stopped."""
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
        if self.web_server is not None:
            self.web_server.stop()
            self.web_server = None
        stopped = True
        if self.telegram_background_worker is not None:
            stopped = self.telegram_background_worker.stop(timeout=timeout)
        clear_component_status()
        self._stopped.set()
        return stopped

    def wait(self, *, timeout: float | None = None) -> bool:
        """Wait until shutdown is requested and return whether the gateway has stopped."""
        return self._stopped.wait(timeout)

    def _start_web(self, logger: logging.Logger) -> None:
        """Serve the shared web app (health probes + alert intake) in a daemon thread."""
        from core.domain.alerts.inbox import AlertInbox, set_current_inbox
        from gateway.web_server import serve_webapp_in_thread

        set_current_inbox(AlertInbox())
        port = int(os.environ.get("PORT", "8000"))
        try:
            handle = serve_webapp_in_thread(host="0.0.0.0", port=port)
        except RuntimeError as exc:
            logger.warning("web app disabled: %s", exc)
            self.components["web"] = f"failed ({exc})"
            return
        self.web_server = handle
        self.components["web"] = f"serving http://{handle.bound_address} (health, alerts)"
        logger.info("web app serving on http://%s", handle.bound_address)

    def _start_telegram(self, logger: logging.Logger, handler: Any) -> None:
        """Start the Telegram chat worker; run without it when not configured."""
        try:
            worker, settings = start_telegram_worker(logger=logger, handler=handler)
        except GatewayConfigurationError as exc:
            logger.warning("Telegram chat disabled: %s", exc)
            self.components["telegram"] = f"not configured ({exc})"
            return
        self.settings = settings
        self.telegram_background_worker = worker
        self.components["telegram"] = "polling for messages"

    def _start_scheduler(self, _logger: logging.Logger) -> None:
        """Run cron-scheduled tasks inside the daemon (no separate process needed)."""
        from integrations.sentry.scheduler_bootstrap import install as install_sentry_runner
        from platform.scheduler.runner import start_background_scheduler
        from tools.investigation.scheduler_bootstrap import install as install_scheduler_runner

        install_scheduler_runner()
        install_sentry_runner()
        scheduler, task_count = start_background_scheduler()
        if scheduler is None:
            self.components["scheduler"] = "idle (no scheduled tasks)"
            return
        self.scheduler = scheduler
        self.components["scheduler"] = f"running {task_count} scheduled task(s)"

    def _publish_status(self, logger: logging.Logger) -> None:
        GATEWAY_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        GATEWAY_PID_FILE.write_text(f"{os.getpid()}\n")
        write_component_status(self.components)
        for name, detail in self.components.items():
            logger.info("component %s: %s", name, detail)

    def _handle_signal(self, *_args: object) -> None:
        self.stop()


def start_gateway(*, wait: bool = True) -> GatewayManager:
    """Compatibility wrapper for existing CLI/import callers."""
    return GatewayManager().start_gateway(wait=wait)


def main() -> None:
    GatewayManager().start_gateway()


if __name__ == "__main__":
    main()
