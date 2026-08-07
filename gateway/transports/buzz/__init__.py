"""Buzz poll transport for the gateway.

Inbound Buzz messaging: settings, mention-feed poller, inbound authorization,
the edit-in-place output sink, approvals, and the background worker. The
per-message handler it drives is transport-agnostic and injected by the
composition root (:mod:`gateway.core.runtime.manager`). Mirrors
:mod:`gateway.transports.telegram` (poll-based, not a persistent socket).

Transport entry: :mod:`gateway.transports.buzz.wiring` (``start_buzz_worker``).
"""

from __future__ import annotations
