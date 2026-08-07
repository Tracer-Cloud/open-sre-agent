"""Uniform start/stop contract for chat transports.

Owned by :mod:`gateway.channels` (the consumer composer). Peer packages under
``gateway.transports`` expose ``startup.start_*_worker`` only — they do not
compose each other. Anything specific to one transport (readiness waits,
settings shapes) stays in that transport's ``startup`` module.

A transport that is not configured is not an error: the gateway runs with
whichever transports have credentials.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from gateway.core.runtime.errors import (
    GatewayConfigurationError,
    GatewayTransportFailedError,
)
from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.transports.buzz.startup import start_buzz_worker
from gateway.transports.discord.startup import start_discord_worker
from gateway.transports.slack.startup import start_slack_worker
from gateway.transports.telegram.startup import start_telegram_worker

# How long a shutdown waits on each worker before giving up on it.
DEFAULT_STOP_TIMEOUT_SECONDS = 8.0


class TransportName(StrEnum):
    """Chat transports the gateway can serve.

    Doubles as the key in :attr:`gateway.channels.ChannelsHandle.transports`
    and in the component status map, so status keys and lookups cannot drift.
    Web is not a member: it is a channel but not a chat transport.
    """

    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"
    BUZZ = "buzz"


class TransportWorker(Protocol):
    """Background worker owning one transport's connection."""

    def stop(self, *, timeout: float = ...) -> bool:
        """Stop the worker and return whether it shut down within ``timeout``."""


# Each transport's startup returns its worker plus its own settings object.
TransportStarter = Callable[..., tuple[TransportWorker, Any]]


@dataclass(frozen=True)
class TransportSpec:
    """How to start one transport and what to report once it is running."""

    name: TransportName
    start: TransportStarter
    running_status: str


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


TRANSPORTS: tuple[TransportSpec, ...] = (
    TransportSpec(TransportName.TELEGRAM, start_telegram_worker, "polling for messages"),
    TransportSpec(TransportName.SLACK, start_slack_worker, "connected via socket mode"),
    TransportSpec(TransportName.DISCORD, start_discord_worker, "connected via gateway"),
    TransportSpec(TransportName.BUZZ, start_buzz_worker, "polling for messages"),
)


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


__all__ = [
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "TRANSPORTS",
    "ChatStartup",
    "TransportHandle",
    "TransportName",
    "TransportSpec",
    "TransportWorker",
    "start_transports",
    "stop_transports",
]
