"""Local-host pub/sub bus for cross-agent findings over a Unix-domain socket.

Carries the same shape as ``core/state/models.py``'s ``evidence`` records so
findings published by one agent (claude-code, cursor, aider, ...) can later be
lifted into ``AgentState.evidence`` without re-mapping fields. See
``docs/fleet.mdx`` for the on-the-wire schema.

Topology is a self-electing broker: the first ``publish`` or ``subscribe`` call
that finds no live socket binds it and runs an in-process daemon thread that
fans incoming JSONL messages out to every connected subscriber. Other processes
attach as plain clients. If the broker dies, the next operation re-elects.
"""

from __future__ import annotations

from tools.system.fleet_monitoring.bus.api import (
    DEFAULT_BUS_SOCKET_PATH,
    publish,
    subscribe,
)
from tools.system.fleet_monitoring.bus.message import (
    BUS_SCHEMA_VERSION,
    BusMessage,
)
from tools.system.fleet_monitoring.bus.server import BusServer

__all__ = [
    "BUS_SCHEMA_VERSION",
    "BusMessage",
    "BusServer",
    "DEFAULT_BUS_SOCKET_PATH",
    "publish",
    "subscribe",
]
