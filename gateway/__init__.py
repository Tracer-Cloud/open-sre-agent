"""Standalone messaging gateway for inbound chat platforms.

Subpackages:

* ``core/`` — process + leaf infrastructure (``runtime``, ``storage``,
  ``billing``, ``attachments``, ``session``, ``config``)
* ``transports/`` — chat peers (``slack``, ``discord``, ``telegram``)
* ``web/`` — web surface (FastAPI health / alerts / investigations; not a chat transport)

Entry points:

* Production — ``opensre gateway start`` (CLI wires slash ports into ``GatewayController``)
* Package main — ``python -m gateway`` fails closed (guard in ``__main__.py``)
* Composition root — :mod:`gateway.core.runtime.controller`
* Daemon helpers — :mod:`gateway.core.runtime.daemon`
* HTTP app (``MODE=web``) — :mod:`gateway.web.webapp` (``app``)
* Telegram — :mod:`gateway.transports.telegram.startup`
* Slack — :mod:`gateway.transports.slack.startup`
* Discord — :mod:`gateway.transports.discord.startup`
"""

from __future__ import annotations

__all__: list[str] = []
