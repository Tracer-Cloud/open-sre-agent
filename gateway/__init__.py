"""Standalone messaging gateway for inbound chat platforms.

Entry points (start here):

* Package main — :mod:`gateway.main` / ``gateway/main.py``
  (``python -m gateway.main`` or ``opensre gateway start``).
* Composition root (implementation) — :mod:`gateway.runtime.manager`.
* Daemon helpers (pidfile / status) — :mod:`gateway.runtime.daemon`.
* HTTP app (``MODE=web``) — :mod:`gateway.http.webapp` (``app``).
* Telegram transport — :mod:`gateway.telegram.wiring` (``start_telegram_worker``).
* Slack transport — :mod:`gateway.slack.wiring` (``start_slack_worker``).

See ``gateway/README.md`` § Entry points.
"""

from __future__ import annotations

__all__: list[str] = []
