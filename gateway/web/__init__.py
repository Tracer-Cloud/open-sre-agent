"""Web surface — FastAPI app, API routes, investigation persistence.

Primary entry: :mod:`gateway.web.webapp` (``app``) — used by
``uvicorn gateway.web.webapp:app`` when ``MODE=web``, and by the gateway
daemon / interactive shell via :mod:`gateway.web.web_server`.

Not a chat transport: no turn-handler / sink wiring. May import
``gateway.core``; must not import ``gateway.transports``.
"""

from __future__ import annotations
