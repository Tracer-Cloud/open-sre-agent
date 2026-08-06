"""Standalone messaging gateway for inbound chat platforms.

Subpackages (see ``gateway/AGENTS.md``):

* ``core/`` — process + leaf infrastructure (``runtime``, ``storage``,
  ``billing``, ``attachments``, ``session``, ``config``)
* ``transports/`` — chat peers (``slack``, ``discord``, ``telegram``)
* ``web/`` — web surface (FastAPI health / alerts / investigations; not a chat transport)

Entry points:

* Package main — :mod:`gateway.main` (``python -m gateway.main``)
* Composition root — :mod:`gateway.core.runtime.manager`
* Daemon helpers — :mod:`gateway.core.runtime.daemon`
* HTTP app (``MODE=web``) — :mod:`gateway.web.webapp` (``app``)
* Telegram — :mod:`gateway.transports.telegram.wiring`
* Slack — :mod:`gateway.transports.slack.wiring`
* Discord — :mod:`gateway.transports.discord.wiring`

See ``gateway/README.md`` § Entry points.
"""

from __future__ import annotations

__all__: list[str] = []
