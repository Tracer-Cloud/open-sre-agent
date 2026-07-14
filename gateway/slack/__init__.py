"""Slack Socket Mode transport for the gateway.

Inbound Slack messaging: settings, event parsing, inbound authorization,
the thread-reply output sink, and the Socket Mode background worker. The
per-message handler it drives is transport-agnostic and injected by the
composition root (:mod:`gateway.runtime.manager`). Outbound-only Slack delivery
(webhooks, RCA reports) lives in :mod:`integrations.slack`.

Transport entry: :mod:`gateway.slack.wiring` (``start_slack_worker``).
"""

from __future__ import annotations
