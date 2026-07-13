"""Everything the gateway serves over HTTP: web app, API routes, investigation persistence.

Primary entry: :mod:`gateway.http.webapp` (``app``) — used by
``uvicorn gateway.http.webapp:app`` when ``MODE=web``.
"""

from __future__ import annotations
