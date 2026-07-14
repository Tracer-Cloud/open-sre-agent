"""Telegram long-poll transport for the gateway.

Inbound Telegram messaging: settings, poller, inbound authorization, the
edit-in-place output sink, and the background worker. The per-message handler
it drives is transport-agnostic and injected by the composition root
(:mod:`gateway.runtime.manager`). Mirrors :mod:`gateway.slack`.

Transport entry: :mod:`gateway.telegram.wiring` (``start_telegram_worker``).
"""

from __future__ import annotations
