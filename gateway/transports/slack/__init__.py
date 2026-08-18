"""Slack Socket Mode transport for the gateway.

Layout:
- ``inbound/`` — event parsing, authorization, attachments, dispatcher
- ``outbound/`` — thread-reply sink, approvals, feedback, streamed replies
- ``connection/`` — Socket Mode worker and heartbeat
- package root — ``settings``, ``client``, ``startup``

The per-message handler the worker drives is transport-agnostic and injected by
the composition root (:mod:`gateway.core.runtime.controller`). Outbound-only Slack
delivery (webhooks, RCA reports) lives in :mod:`integrations.slack`.

Transport entry: :mod:`gateway.transports.slack.startup` (``start_slack_worker``).
"""

from __future__ import annotations
