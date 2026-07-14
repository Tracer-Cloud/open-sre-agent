"""Slack bot surface.

The inbound Slack transport lives in :mod:`gateway.slack` (Socket Mode worker,
settings, event parsing, output sink), wired by :mod:`gateway.runtime.manager` — the
layering contract forbids ``gateway`` → ``surfaces`` imports, so the transport
sits beside the Telegram worker in ``gateway/``. Outbound Slack delivery
(webhooks, RCA reports) lives in :mod:`integrations.slack`.

This package is reserved for a future user-facing Slack surface (slash-command
UI, home tab); it intentionally holds no code today.
"""

from __future__ import annotations
