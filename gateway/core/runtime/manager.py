"""Gateway process entrypoint and lifecycle owner.

``GatewayManager`` is the composition root for the OpenSRE background agent:
logging + credential hydrate, then
:func:`bootstrap.process.configure_process` (``GATEWAY_PROFILE``), then
assemble the turn handler and start daemon components — web, Telegram / Slack /
Discord / Buzz (when configured), and the scheduled-task runner. Owns signals and
``stop``/``wait``. Component states go through
:func:`gateway.core.runtime.daemon.write_component_status`. Transport and turn
dispatch live in :mod:`gateway.core.runtime.turn_handler` and the transport
wiring packages
(:mod:`gateway.transports.telegram.wiring`,
:mod:`gateway.transports.slack.wiring`,
:mod:`gateway.transports.discord.wiring`,
:mod:`gateway.transports.buzz.wiring`) — not here.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable
from typing import Any

from rich.console import Console

from gateway.core.config.logging_config import configure_logging
from gateway.core.runtime.concurrency import (
    ConcurrencyLimitedTurnHandler,
    TurnConcurrencyGate,
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
from gateway.core.runtime.turn_handler import GatewayTurnHandler
from gateway.transports.buzz.background import BuzzGatewayBackground
from gateway.transports.buzz.wiring import start_buzz_worker
from gateway.transports.discord.background import DiscordGatewayBackground
from gateway.transports.discord.wiring import start_discord_worker
from gateway.transports.slack.socket_mode_worker import SlackGatewayBackground
from gateway.transports.slack.wiring import start_slack_worker
from gateway.transports.telegram.background import TelegramGatewayBackground
from gateway.transports.telegram.settings import GatewaySettings
from gateway.transports.telegram.wiring import start_telegram_worker

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
        self.settings: GatewaySettings | None = None
        self.logger: logging.Logger | None = None
        self.telegram_background_worker: TelegramGatewayBackground | None = None
        self.slack_background_worker: SlackGatewayBackground | None = None
        self.discord_background_worker: DiscordGatewayBackground | None = None
        self.buzz_background_worker: BuzzGatewayBackground | None = None
        self.web_server: Any = None
        self.scheduler: Any = None
        self._scheduler_reload_thread: threading.Thread | None = None
        self.components: dict[str, str] = {}
        self._slash_ports_factory = slash_ports_factory
        self._credential_hydrator_factory = (
            credential_hydrator_factory or GatewayCredentialHydrator.from_environment
        )
        profile = os.getenv("OPENSRE_SIZE_PROFILE", "SMALL").strip().upper()
        self.turn_gate = turn_gate or TurnConcurrencyGate.for_profile(profile)
        self._stopped = threading.Event()

    def start_gateway(self, *, wait: bool = True) -> GatewayManager:
        """Credential hydrate, shared process boot, then start daemon components."""
        from bootstrap.process import GATEWAY_PROFILE, configure_process

        logger = self.logger = configure_logging()
        set_ready(False)
        self._load_credentials(logger)
        configure_process(GATEWAY_PROFILE, logger=logger)

        # Compose the transport-agnostic turn handler. Action tools are resolved
        # per turn from each chat's live session inside the handler (not here).
        console = Console(force_terminal=False)
        handler = GatewayTurnHandler(
            console=console,
            slash_ports_factory=self._slash_ports_factory,
        )
        chat_handler = ConcurrencyLimitedTurnHandler(
            handler=handler,
            gate=self.turn_gate,
        )

        self._start_web(logger)
        self._start_telegram(logger, chat_handler)
        self._start_slack(logger, chat_handler)
        self._start_discord(logger, chat_handler)
        self._start_buzz(logger, chat_handler)
        self._start_scheduler(logger)
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

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Shut down all components and return whether the chat worker stopped."""
        set_ready(False)
        self._stopped.set()
        stopped = True
        if self._scheduler_reload_thread is not None:
            self._scheduler_reload_thread.join(timeout=min(timeout, 2.0))
            self._scheduler_reload_thread = None
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
        if self.web_server is not None:
            self.web_server.stop()
            self.web_server = None
        if self.telegram_background_worker is not None:
            stopped = self.telegram_background_worker.stop(timeout=timeout) and stopped
        if self.slack_background_worker is not None:
            stopped = self.slack_background_worker.stop(timeout=timeout) and stopped
        if self.discord_background_worker is not None:
            stopped = self.discord_background_worker.stop(timeout=timeout) and stopped
        if self.buzz_background_worker is not None:
            stopped = self.buzz_background_worker.stop(timeout=timeout) and stopped
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

    def _start_web(self, logger: logging.Logger) -> None:
        """Serve the shared web app (health probes + alert intake) in a daemon thread."""
        from core.domain.alerts.inbox import AlertInbox, set_current_inbox
        from gateway.web.web_server import serve_webapp_in_thread

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

    def _start_slack(self, logger: logging.Logger, handler: Any) -> None:
        """Start the Slack chat worker; run without it when not configured."""
        try:
            worker, _settings = start_slack_worker(logger=logger, handler=handler)
        except GatewayConfigurationError as exc:
            logger.warning("Slack chat disabled: %s", exc)
            self.components["slack"] = f"not configured ({exc})"
            return
        self.slack_background_worker = worker
        self.components["slack"] = "connected via socket mode"

    def _start_discord(self, logger: logging.Logger, handler: Any) -> None:
        """Start the Discord chat worker; run without it when not configured."""
        try:
            worker, settings = start_discord_worker(logger=logger, handler=handler)
        except GatewayConfigurationError as exc:
            logger.warning("Discord chat disabled: %s", exc)
            self.components["discord"] = f"not configured ({exc})"
            return
        if not worker.wait_until_ready(timeout=settings.startup_timeout_seconds):
            logger.warning(
                "Discord gateway did not become ready within %.0fs",
                settings.startup_timeout_seconds,
            )
            worker.stop()
            self.components["discord"] = "failed (startup timeout)"
            return
        self.discord_background_worker = worker
        self.components["discord"] = "connected via gateway"

    def _start_buzz(self, logger: logging.Logger, handler: Any) -> None:
        """Start the Buzz chat worker; run without it when not configured."""
        try:
            worker, _settings = start_buzz_worker(logger=logger, handler=handler)
        except GatewayConfigurationError as exc:
            logger.warning("Buzz chat disabled: %s", exc)
            self.components["buzz"] = f"not configured ({exc})"
            return
        self.buzz_background_worker = worker
        self.components["buzz"] = "polling for messages"

    def _start_scheduler(self, _logger: logging.Logger) -> None:
        """Run cron-scheduled tasks inside the daemon (no separate process needed)."""
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
        self._start_scheduler_reload_watcher(_logger)

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


def start_gateway(*, wait: bool = True) -> GatewayManager:
    """Compatibility wrapper for existing CLI/import callers.

    Production boot with slash ports goes through
    :mod:`surfaces.cli.gateway_entry` (surfaces may import gateway; the reverse
    is forbidden).
    """
    return GatewayManager().start_gateway(wait=wait)


def main() -> None:
    GatewayManager().start_gateway()


if __name__ == "__main__":
    main()
