"""Start and stop everything the gateway serves users through.

The one file allowed to import every transport: it holds the transport
registry and composes web + chat into a single running handle. Mirrors each
transport's own ``startup.py`` one level up — those start one platform, this
starts the gateway.

Only the composition root (:class:`~gateway.core.runtime.manager.GatewayManager`)
imports this module. Transports never import it back, and ``gateway.core``
cannot (core must not depend on transports). The scheduler is not started
here; the manager starts it as a peer.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from gateway.core.runtime.errors import (
    GatewayConfigurationError,
    GatewayTransportFailedError,
)
from gateway.core.transport_api import (
    GatewayAgentCallback,
    TransportName,
    TransportSpec,
    TransportWorker,
)
from gateway.transports.buzz.startup import start_buzz_worker
from gateway.transports.discord.startup import start_discord_worker
from gateway.transports.slack.startup import start_slack_worker
from gateway.transports.telegram.startup import start_telegram_worker
from gateway.web.startup import start_web_server
from gateway.web.web_server import WebAppServerHandle

# How long a shutdown waits on each worker before giving up on it.
DEFAULT_STOP_TIMEOUT_SECONDS = 8.0

# The web app is a thread join, not a network drain, so it gets a smaller slice
# of the shutdown budget and leaves the rest for in-flight chat turns.
WEB_STOP_TIMEOUT_SECONDS = 5.0

_WEB_COMPONENT = "web"


TRANSPORTS: tuple[TransportSpec, ...] = (
    TransportSpec(TransportName.TELEGRAM, start_telegram_worker, "polling for messages"),
    TransportSpec(TransportName.SLACK, start_slack_worker, "inbound connected"),
    TransportSpec(TransportName.DISCORD, start_discord_worker, "connected via gateway"),
    TransportSpec(TransportName.BUZZ, start_buzz_worker, "polling for messages"),
)


@dataclass(frozen=True)
class TransportHandle:
    """A started transport: the worker to stop, and how to describe it."""

    name: TransportName
    worker: TransportWorker
    status: str


@dataclass(frozen=True)
class ChatStartup:
    """Started chat transports plus the status of every transport that was tried.

    ``statuses`` covers transports that did not start, so the caller can report
    "not configured" without the callee reaching into its status map.
    """

    handles: list[TransportHandle]
    statuses: dict[TransportName, str]


def start_transports(
    *,
    logger: logging.Logger,
    handler: GatewayAgentCallback,
) -> ChatStartup:
    """Start every configured transport and report what each one did.

    * :class:`GatewayConfigurationError` → ``not configured (…)`` (skipped).
    * :class:`GatewayTransportFailedError` → ``failed (…)`` (skipped).

    The gateway still serves whichever transports started successfully.
    """
    handles: list[TransportHandle] = []
    statuses: dict[TransportName, str] = {}
    for spec in TRANSPORTS:
        try:
            worker, _settings = spec.start(logger=logger, handler=handler)
        except GatewayConfigurationError as exc:
            logger.warning("%s chat disabled: %s", spec.name.capitalize(), exc)
            statuses[spec.name] = f"not configured ({exc})"
            continue
        except GatewayTransportFailedError as exc:
            logger.warning("%s chat failed: %s", spec.name.capitalize(), exc)
            statuses[spec.name] = f"failed ({exc})"
            continue
        handles.append(TransportHandle(name=spec.name, worker=worker, status=spec.running_status))
        statuses[spec.name] = spec.running_status
    return ChatStartup(handles=handles, statuses=statuses)


def stop_transports(
    *,
    handles: Sequence[TransportHandle],
    timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
) -> bool:
    """Stop every started transport and return whether all of them stopped.

    Every worker is asked to stop even after one fails, so a single stuck
    transport cannot leave the others running.
    """
    stopped = True
    for handle in handles:
        stopped = handle.worker.stop(timeout=timeout) and stopped
    return stopped


@dataclass
class StartedGateway:
    """The running gateway: optional web server, chat transports, statuses."""

    web_server: WebAppServerHandle | None = None
    transports: dict[TransportName, TransportHandle] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> bool:
        """Stop web and every chat transport; return whether all chat workers stopped."""
        if self.web_server is not None:
            self.web_server.stop(timeout=min(timeout, WEB_STOP_TIMEOUT_SECONDS))
            self.web_server = None
        stopped = stop_transports(handles=list(self.transports.values()), timeout=timeout)
        self.transports = {}
        return stopped


def start_gateway(
    *,
    logger: logging.Logger,
    handler: GatewayAgentCallback,
) -> StartedGateway:
    """Start web and every chat transport together.

    Missing chat credentials skip that transport (``not configured``); readiness
    or runtime failures record ``failed``. The rest still start.
    """
    web = start_web_server(logger=logger)
    chat = start_transports(logger=logger, handler=handler)
    statuses: dict[str, str] = {_WEB_COMPONENT: web.status}
    for name, status in chat.statuses.items():
        statuses[name] = status
    return StartedGateway(
        web_server=web.server,
        transports={handle.name: handle for handle in chat.handles},
        statuses=statuses,
    )


__all__ = [
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "TRANSPORTS",
    "WEB_STOP_TIMEOUT_SECONDS",
    "ChatStartup",
    "StartedGateway",
    "TransportHandle",
    "start_gateway",
    "start_transports",
    "stop_transports",
]
