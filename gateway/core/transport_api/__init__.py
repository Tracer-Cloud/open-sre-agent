"""The API every chat transport implements.

In API-framework terms the gateway splits like this:

* ``gateway/transports/<name>/`` — the ingress adapters: platform inbound that
  authorizes, resolves a session, builds a sink, and calls the turn handler.
* ``gateway/core/middleware/`` — the shared per-turn steps every ingress
  adapter runs (inbound decision, identity policy, approvals, stop/cancel, attention,
  conversation locks, terminal outcome).
* ``gateway/core/runtime/`` — the service layer: the turn handler bridging
  into the agent harness, and process plumbing.
* ``gateway/startup.py`` — the facade: the one ``start_gateway`` entry the
  daemon calls. The transport registry and worker start/stop loop live with
  the transports (``gateway/transports/startup.py``).

This package is the contract between them. A transport registers a
:class:`TransportSpec`, its worker satisfies :class:`TransportWorker`, its sink
satisfies :class:`GatewaySink`, and every inbound message ends in one call to a
:data:`GatewayAgentCallback`. Nothing here may import a transport — the
contract is what transports depend on, never the reverse.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from core.agent_harness import OutputSink, SessionCore


class TransportName(StrEnum):
    """Chat transports the gateway can serve.

    Doubles as the key in :attr:`gateway.startup.StartedGateway.transports`
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


@runtime_checkable
class GatewaySink(OutputSink, Protocol):
    """An :class:`OutputSink` with the gateway's per-turn status and final-answer hooks."""

    def set_tool_status(self, text: str) -> None:
        """Show live tool progress for the running turn."""

    def finalize(self, text: str) -> None:
        """Deliver the turn's final answer to the chat."""


# The transport-agnostic per-message callback: ``(text, session, sink, logger)``.
GatewayAgentCallback = Callable[[str, SessionCore, GatewaySink, logging.Logger], None]


__all__ = [
    "GatewayAgentCallback",
    "GatewaySink",
    "TransportName",
    "TransportSpec",
    "TransportStarter",
    "TransportWorker",
]
