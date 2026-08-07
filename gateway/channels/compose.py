"""Start and stop the full consumer channel set: web + chat transports.

The composition root (:class:`~gateway.core.runtime.manager.GatewayManager`)
calls :func:`start_channels` and keeps only the returned handle. The scheduler
is not a channel; the manager starts it as a peer after this module returns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from gateway.channels.chat import (
    DEFAULT_STOP_TIMEOUT_SECONDS,
    TransportHandle,
    TransportName,
    start_transports,
    stop_transports,
)
from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.web.startup import start_web_server
from gateway.web.web_server import WebAppServerHandle

# The web app is a thread join, not a network drain, so it gets a smaller slice
# of the shutdown budget and leaves the rest for in-flight chat turns.
WEB_STOP_TIMEOUT_SECONDS = 5.0

_WEB_COMPONENT = "web"


@dataclass
class ChannelsHandle:
    """Running consumer channels: optional web server, chat transports, statuses."""

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


def start_channels(
    *,
    logger: logging.Logger,
    handler: GatewayAgentCallback,
) -> ChannelsHandle:
    """Start web and every chat transport together.

    Missing chat credentials skip that transport (``not configured``); readiness
    or runtime failures record ``failed``. The rest still start.
    """
    web = start_web_server(logger=logger)
    chat = start_transports(logger=logger, handler=handler)
    statuses: dict[str, str] = {_WEB_COMPONENT: web.status}
    for name, status in chat.statuses.items():
        statuses[name] = status
    return ChannelsHandle(
        web_server=web.server,
        transports={handle.name: handle for handle in chat.handles},
        statuses=statuses,
    )


__all__ = [
    "WEB_STOP_TIMEOUT_SECONDS",
    "ChannelsHandle",
    "start_channels",
]
