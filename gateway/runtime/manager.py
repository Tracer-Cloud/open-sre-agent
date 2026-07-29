"""Gateway process entrypoint and lifecycle owner.

``GatewayManager`` is the composition root for the OpenSRE background agent:
it assembles the transport-agnostic turn handler from a booted session's tools
and starts every daemon component — the web health app, the Telegram and Slack
chat workers (when configured), and the scheduled-task runner — then owns the
process lifecycle (signals, ``stop``/``wait``). Component states are published
through :func:`gateway.runtime.daemon.write_component_status` so the CLI and the
interactive shell can report status. It holds no transport or agent-dispatch
logic itself — those live in :mod:`gateway.runtime.turn_handler`,
:mod:`gateway.telegram.wiring`, and :mod:`gateway.slack.wiring`, and
:mod:`gateway.discord.wiring`.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.llm.internal.preload import preload_llm_clients
from gateway.config.configure_gateway_logging import configure_gateway_logging
from gateway.discord.background import DiscordGatewayBackground
from gateway.discord.wiring import start_discord_worker
from gateway.runtime.concurrency import (
    ConcurrencyLimitedTurnHandler,
    TurnConcurrencyGate,
)
from gateway.runtime.credential_hydration import (
    GatewayBootstrap,
    GatewayCredentialHydrator,
)
from gateway.runtime.daemon import (
    GATEWAY_PID_FILE,
    clear_component_status,
    write_component_status,
)
from gateway.runtime.errors import GatewayConfigurationError
from gateway.runtime.readiness import set_gateway_ready
from gateway.runtime.remote_run_worker import (
    RemoteRunWorker,
    build_remote_run_worker,
)
from gateway.runtime.turn_handler import GatewayTurnHandler
from gateway.slack.socket_mode_worker import SlackGatewayBackground
from gateway.slack.wiring import start_slack_worker
from gateway.telegram.background import TelegramGatewayBackground
from gateway.telegram.settings import GatewaySettings
from gateway.telegram.wiring import start_telegram_worker

SlashPortsFactory = Callable[[], Any]
CredentialHydratorFactory = Callable[[], GatewayCredentialHydrator | None]
RemoteRunWorkerFactory = Callable[
    [str, str, Any, TurnConcurrencyGate, logging.Logger],
    RemoteRunWorker,
]


class GatewayManager:
    """Composition root and lifecycle handle for the running gateway process."""

    def __init__(
        self,
        *,
        slash_ports_factory: SlashPortsFactory | None = None,
        credential_hydrator_factory: CredentialHydratorFactory | None = None,
        remote_run_worker_factory: RemoteRunWorkerFactory | None = None,
        turn_gate: TurnConcurrencyGate | None = None,
    ) -> None:
        self.settings: GatewaySettings | None = None
        self.logger: logging.Logger | None = None
        self.telegram_background_worker: TelegramGatewayBackground | None = None
        self.slack_background_worker: SlackGatewayBackground | None = None
        self.discord_background_worker: DiscordGatewayBackground | None = None
        self.web_server: Any = None
        self.scheduler: Any = None
        self.remote_run_worker: RemoteRunWorker | None = None
        self.components: dict[str, str] = {}
        self._slash_ports_factory = slash_ports_factory
        self._credential_hydrator_factory = (
            credential_hydrator_factory or GatewayCredentialHydrator.from_environment
        )
        self._remote_run_worker_factory = remote_run_worker_factory
        profile = os.getenv("OPENSRE_SIZE_PROFILE", "SMALL").strip().upper()
        self.turn_gate = turn_gate or TurnConcurrencyGate.for_profile(profile)
        self._stopped = threading.Event()

    def start_gateway(self, *, wait: bool = True) -> GatewayManager:
        """Assemble the turn handler, start all components, and own the lifecycle."""
        from integrations.harness_adapters import register_harness_adapters as register_integrations
        from tools.harness_adapters import register_harness_adapters as register_tools

        logger = self.logger = configure_gateway_logging()
        set_gateway_ready(False)
        bootstrap = self._hydrate_credentials(logger)

        harness = AgentHarness(HarnessConfig(open_storage=False))
        harness.resolve_env_variables()
        # Mirror shell boot: register harness adapters here (gateway cannot import
        # surfaces.boundary without a surfaces↔gateway peer import).
        register_integrations()
        register_tools()
        # Env-gated (OPENSRE_NO_TELEMETRY / DO_NOT_TRACK / missing DSN) — free when off.
        from platform.observability.errors.sentry import init_sentry

        init_sentry(entrypoint="gateway")
        # Load the LLM client graph as one snapshot at boot (avoids a stale
        # mixed-version process after a code change).
        preload_llm_clients()

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

        # A configured durable worker is mandatory. Start it before components
        # with externally visible side effects so a bad Neon bootstrap fails closed.
        self._start_remote_runs(logger, handler, bootstrap)
        self._start_web(logger)
        self._start_telegram(logger, chat_handler)
        self._start_slack(logger, chat_handler)
        self._start_discord(logger, chat_handler)
        self._start_scheduler(logger)
        self._publish_status(logger)
        # Deploy health waits (EC2 Docker + AMI) match this line for Telegram
        # and/or Slack — do not rely on transport-specific log strings alone.
        logger.info("[gateway] ready")
        set_gateway_ready(True)

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        if wait:
            self.wait()
        return self

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Shut down all components and return whether the chat worker stopped."""
        set_gateway_ready(False)
        stopped = True
        if self.remote_run_worker is not None:
            stopped = self.remote_run_worker.stop(timeout=timeout)
            self.remote_run_worker = None
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
        clear_component_status()
        self._stopped.set()
        return stopped

    def _hydrate_credentials(self, logger: logging.Logger) -> GatewayBootstrap | None:
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
        from gateway.http.web_server import serve_webapp_in_thread

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

    def _start_scheduler(self, _logger: logging.Logger) -> None:
        """Run cron-scheduled tasks inside the daemon (no separate process needed)."""
        from integrations.sentry.scheduler_bootstrap import install as install_sentry_runner
        from platform.scheduler.runner import start_background_scheduler
        from tools.investigation.scheduler_bootstrap import install as install_scheduler_runner

        install_scheduler_runner()
        install_sentry_runner()
        from gateway.runtime.scheduler_concurrency import gate_registered_scheduler_runners

        gate_registered_scheduler_runners(self.turn_gate)
        scheduler, task_count = start_background_scheduler()
        if scheduler is None:
            self.components["scheduler"] = "idle (no scheduled tasks)"
            return
        self.scheduler = scheduler
        self.components["scheduler"] = f"running {task_count} scheduled task(s)"

    def _start_remote_runs(
        self,
        logger: logging.Logger,
        handler: Any,
        bootstrap: GatewayBootstrap | None,
    ) -> None:
        """Start the Neon run worker when a bootstrap bundle supplies its DSN."""
        organization_id = os.getenv("ORGANIZATION_ID", "").strip()
        database_url = bootstrap.database_url if bootstrap is not None else None
        if not organization_id or not database_url:
            self.components["api_runs"] = "not configured"
            return
        factory = self._remote_run_worker_factory
        if factory is None:

            def factory(
                org: str,
                dsn: str,
                callback: Any,
                gate: TurnConcurrencyGate,
                log: logging.Logger,
            ) -> RemoteRunWorker:
                return build_remote_run_worker(
                    organization_id=org,
                    database_url=dsn,
                    handler=callback,
                    gate=gate,
                    logger=log,
                )

        try:
            worker = factory(
                organization_id,
                database_url,
                handler,
                self.turn_gate,
                logger,
            )
            worker.start()
        except Exception as exc:
            logger.error("remote run worker startup failed (%s)", type(exc).__name__)
            raise GatewayConfigurationError("Remote run worker startup failed") from None
        self.remote_run_worker = worker
        self.components["api_runs"] = "polling"

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
