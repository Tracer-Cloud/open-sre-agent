"""Slack Socket Mode transport for the gateway.

Inbound Slack messaging: settings, event parsing, inbound authorization,
the thread-reply output sink, and the Socket Mode background worker. The
per-message handler it drives is transport-agnostic and injected by the
composition root (:mod:`gateway.manager`). Outbound-only Slack delivery
(webhooks, RCA reports) lives in :mod:`integrations.slack`.
"""

from __future__ import annotations
