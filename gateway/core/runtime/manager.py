"""Gateway process entrypoint and lifecycle owner.

``GatewayManager`` is the composition root for the OpenSRE background agent:
logging + credential hydrate, then
:func:`bootstrap.process.configure_process` (``GATEWAY_PROFILE``), then
assemble the turn handler and start components —

* :meth:`start_channels` — web + chat transports via :mod:`gateway.channels`
* :meth:`start_scheduler` — peer of the consumer surfaces (cron / loops)

Owns signals and ``stop``/``wait``. Component states go through
:func:`gateway.core.runtime.daemon.write_component_status`. Channel start/stop
lives in :mod:`gateway.channels`; turn dispatch lives in
:mod:`gateway.core.runtime.turn_handler` — not here.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable
from typing import Any

from rich.console import Console

from gateway import channels as gateway_channels
from gateway.core.config.logging_config import configure_logging
from gateway.core.runtime.concurrency import (
    TurnConcurrencyGate,
    process_turn_gate,
    set_process_turn_gate,
)
from gateway.core.runtime.credential_hydration import (
    GatewayBootstrap,
    GatewayCredentialHydrator,
)
from gateway.core.runtime.daemon import (
    GATEWAY_PID_FILE,
    clear_component_status,
    write_component_status,
)
from gateway.core.runtime.errors import GatewayConfigurationError
from gateway.core.runtime.readiness import set_ready
from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.core.runtime.turn_handler import GatewayTurnHandler

# The reload watcher only polls a flag, so it should never need the full
# shutdown budget; cap it so chat workers keep the rest.
SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS = 2.0

SlashPortsFactory = Callable[[], Any]
CredentialHydratorFactory = Callable[[], GatewayCredentialHydrator | None]


class GatewayManager:
    """Composition root and lifecycle handle for the running gateway process."""

    def __init__(
        self,
        *,
        slash_ports_factory: SlashPortsFactory | None = None,
        credential_hydrator_factory: CredentialHydratorFactory | None = None,
        turn_gate: TurnConcurrencyGate | None = None,
    ) -> None:
        self.logger: logging.Logger | None = None
        self.channels: gateway_channels.ChannelsHandle | None = None
        self.scheduler: Any = None
        self._scheduler_reload_thread: threading.Thread | None = None
        self.components: dict[str, str] = {}
        self._slash_ports_factory = slash_ports_factory
        self._credential_hydrator_factory = (
            credential_hydrator_factory or GatewayCredentialHydrator.from_environment
        )
        if turn_gate is not None:
            set_process_turn_gate(turn_gate)
            self.turn_gate = turn_gate
        else:
            # Same instance Path-2 investigate / InvestigationWorker use.
            self.turn_gate = process_turn_gate()
        self._stopped = threading.Event()

    def start_gateway(self, *, wait: bool = True) -> GatewayManager:
        """Credential hydrate, shared process boot, then channels + scheduler."""
        from bootstrap.process import GATEWAY_PROFILE, configure_process

        logger = self.logger = configure_logging()
        set_ready(False)
        self._load_credentials(logger)
        configure_process(GATEWAY_PROFILE, logger=logger)

        # One turn handler for every chat transport. Capacity gate lives on the
        # same object — do not wrap it in a second "turn handler". Action tools
        # resolve per turn from each chat's live session inside the handler.
        console = Console(force_terminal=False)
        handler = GatewayTurnHandler(
            console=console,
            slash_ports_factory=self._slash_ports_factory,
            gate=self.turn_gate,
        )

        self.start_channels(logger=logger, handler=handler)
        self.start_scheduler(logger=logger)
        self._publish_status(logger)
        # Deploy health waits (EC2 Docker + AMI) match this line for Telegram
        # and/or Slack — do not rely on transport-specific log strings alone.
        logger.info("[gateway] ready")
        set_ready(True)

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        if wait:
            self.wait()
        return self

    def start_channels(
        self,
        *,
        logger: logging.Logger,
        handler: GatewayAgentCallback,
    ) -> None:
        """Start web + every chat transport together (via :mod:`gateway.channels`)."""
        self.channels = gateway_channels.start_channels(logger=logger, handler=handler)
        self.components.update(self.channels.statuses)

    def start_scheduler(self, *, logger: logging.Logger) -> None:
        """Host the platform scheduler as a peer of the consumer channels."""
        from bootstrap.adapters import install_scheduler_runners
        from platform.scheduler.reload_signal import consume_scheduler_reload_request
        from platform.scheduler.runner import start_background_scheduler

        # Investigation + multiplexed scheduled-agent runners (Sentry digest, etc.).
        # Adapters already registered at process boot; runners attach with the scheduler.
        install_scheduler_runners()
        from gateway.core.runtime.scheduler_concurrency import gate_registered_scheduler_runners

        gate_registered_scheduler_runners(self.turn_gate)
        # Drop any reload request queued before this process owned the scheduler.
        consume_scheduler_reload_request()
        scheduler, task_count = start_background_scheduler()
        if scheduler is None:
            self.components["scheduler"] = "idle (no scheduled tasks)"
        else:
            self.scheduler = scheduler
            self.components["scheduler"] = f"running {task_count} scheduled task(s)"
        self._start_scheduler_reload_watcher(logger)

    def stop(self, *, timeout: float = gateway_channels.DEFAULT_STOP_TIMEOUT_SECONDS) -> bool:
        """Shut down all components and return whether the chat workers stopped."""
        set_ready(False)
        self._stopped.set()
        stopped = True
        if self._scheduler_reload_thread is not None:
            self._scheduler_reload_thread.join(
                timeout=min(timeout, SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS)
            )
            self._scheduler_reload_thread = None
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
        if self.channels is not None:
            stopped = self.channels.stop(timeout=timeout) and stopped
            self.channels = None
        clear_component_status()
        return stopped

    def _load_credentials(self, logger: logging.Logger) -> GatewayBootstrap | None:
        """Hydrate before any transport, scheduler, or worker can start."""
        try:
            hydrator = self._credential_hydrator_factory()
            if hydrator is None:
                self.components["credentials"] = "not configured"
                return None
            bootstrap = hydrator.hydrate()
        except Exception as exc:
            logger.error("gateway credential hydration failed (%s)", type(exc).__name__)
            self.components["credentials"] = "failed"
            raise GatewayConfigurationError("Gateway credential hydration failed") from None
        self.components["credentials"] = (
            "hydrated" if bootstrap.integrations_hydrated else "preseeded"
        )
        return bootstrap

    def wait(self, *, timeout: float | None = None) -> bool:
        """Wait until shutdown is requested and return whether the gateway has stopped."""
        return self._stopped.wait(timeout)

    def _start_scheduler_reload_watcher(self, logger: logging.Logger) -> None:
        """Poll for cross-process reload requests from `/loops` and cron mutations."""
        if self._scheduler_reload_thread is not None:
            return

        def _watch() -> None:
            from platform.scheduler.reload_signal import (
                RELOAD_POLL_SECONDS,
                consume_scheduler_reload_request,
            )

            while not self._stopped.wait(timeout=RELOAD_POLL_SECONDS):
                if not consume_scheduler_reload_request():
                    continue
                try:
                    self._reload_scheduler(logger)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Scheduler reload failed (%s)",
                        type(exc).__name__,
                    )

        self._scheduler_reload_thread = threading.Thread(
            target=_watch,
            name="opensre-scheduler-reload",
            daemon=True,
        )
        self._scheduler_reload_thread.start()

    def _reload_scheduler(self, logger: logging.Logger) -> None:
        """Resync the live scheduler (or start one) from the current task store."""
        from platform.scheduler.runner import refresh_background_scheduler

        scheduler, task_count = refresh_background_scheduler(self.scheduler)
        self.scheduler = scheduler
        if scheduler is None:
            self.components["scheduler"] = "idle (no scheduled tasks)"
            logger.info("Scheduler idle after reload (no enabled tasks)")
        else:
            self.components["scheduler"] = f"running {task_count} scheduled task(s)"
            logger.info("Scheduler reloaded with %d task(s)", task_count)
        self._publish_status(logger)

    def _publish_status(self, logger: logging.Logger) -> None:
        GATEWAY_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        GATEWAY_PID_FILE.write_text(f"{os.getpid()}\n")
        write_component_status(self.components)
        for name, detail in self.components.items():
            logger.info("component %s: %s", name, detail)

    def _handle_signal(self, *_args: object) -> None:
        self.stop()


_BARE_MANAGER_EXIT = (
    "GatewayManager requires slash_ports_factory for production chat.\n"
    "Start with: opensre gateway start\n"
    "        or: opensre gateway start --foreground\n"
    "Unit tests may construct GatewayManager(...) directly.\n"
)


def start_gateway(
    *,
    wait: bool = True,
    slash_ports_factory: SlashPortsFactory | None = None,
) -> GatewayManager:
    """Compatibility wrapper — requires ``slash_ports_factory`` (fail closed).

    Production boot goes through the CLI composition root
    (``opensre gateway start``), which injects headless slash ports. The
    gateway package must not import the surfaces layer, so bare
    ``GatewayManager()`` here cannot wire them.
    """
    if slash_ports_factory is None:
        raise SystemExit(_BARE_MANAGER_EXIT)
    return GatewayManager(slash_ports_factory=slash_ports_factory).start_gateway(wait=wait)


def main() -> None:
    """Refuse bare manager main — same policy as :mod:`gateway.main`."""
    raise SystemExit(_BARE_MANAGER_EXIT)


if __name__ == "__main__":
    main()
