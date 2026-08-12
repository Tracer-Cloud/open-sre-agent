"""Consumer channels the gateway process serves: web + chat transports.

Sole composer of peer surfaces (``gateway.web`` and
``gateway.transports.{telegram,slack,discord}``). The process composition root
(:mod:`gateway.core.runtime.manager`) calls :func:`start_channels` and holds
:class:`ChannelsHandle` — it does not import transports or web.
"""

from __future__ import annotations

from gateway.channels.chat import (
    DEFAULT_STOP_TIMEOUT_SECONDS,
    TransportHandle,
    TransportName,
)
from gateway.channels.compose import ChannelsHandle, start_channels

__all__ = [
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "ChannelsHandle",
    "TransportHandle",
    "TransportName",
    "start_channels",
]
