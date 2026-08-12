"""Chat transport peers — Slack, Discord, Telegram.

Each transport owns settings, inbound worker, security, output sink, and
``startup.py``. Transports are peers: none imports another. Shared machinery
lives under ``gateway.core``. Consumer composition (starting peers together)
lives in ``gateway.channels``.
"""

from __future__ import annotations

__all__: list[str] = []
