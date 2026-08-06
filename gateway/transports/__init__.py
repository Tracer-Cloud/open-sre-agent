"""Chat transport peers — Slack, Discord, Telegram.

Each transport owns settings, inbound worker, security, output sink, and
``wiring.py``. Transports are peers: none imports another. Shared machinery
lives under ``gateway.core``.
"""

from __future__ import annotations

__all__: list[str] = []
