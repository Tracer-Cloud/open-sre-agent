"""Poll ``buzz feed get`` for mentions and yield normalized inbound messages."""

from __future__ import annotations

import logging
import time

from gateway.transports.buzz.poller.cursor import load_cursor, save_cursor
from gateway.transports.buzz.poller.parse_buzz_event import parse_feed_event
from gateway.transports.buzz.settings import BuzzInboundMessage
from integrations.buzz.client import BuzzClient

logger = logging.getLogger(__name__)

_WARNING_COOLDOWN_SECONDS = 60.0
_FEED_TYPES = "mentions"


class BuzzFeedPoller:
    """Poll the mention feed and yield normalized inbound messages.

    ``feed get --since <ts>`` is inclusive (NIP-01 ``since`` semantics: events
    with ``created_at >= since`` match), so re-polling at the same cursor would
    replay the most recent event forever if the cursor advanced to its exact
    timestamp. ``_seen_at_cursor`` dedupes within that one timestamp instead of
    advancing the cursor past it, which would risk skipping a different event
    that happens to share the same second.
    """

    def __init__(self, client: BuzzClient) -> None:
        self._client = client
        self._since = load_cursor()
        self._seen_at_cursor: set[str] = set()
        self._last_warning_monotonic = 0.0

    def poll_once(self) -> list[BuzzInboundMessage]:
        result = self._client.get_feed(since=self._since, types=_FEED_TYPES)
        if not result["success"]:
            self._log_transient("[buzz-gateway] feed get failed: %s", result["error"])
            return []

        events: list[BuzzInboundMessage] = []
        latest = self._since
        for raw in result["events"]:
            if not isinstance(raw, dict):
                continue
            parsed = parse_feed_event(raw)
            if parsed is None:
                continue
            if parsed.created_at == self._since and parsed.event_id in self._seen_at_cursor:
                continue
            events.append(parsed)
            latest = max(latest, parsed.created_at)

        if latest > self._since:
            self._since = latest
            self._seen_at_cursor = {e.event_id for e in events if e.created_at == latest}
            save_cursor(self._since)
        else:
            self._seen_at_cursor.update(e.event_id for e in events)
        return events

    def _log_transient(self, message: str, *args: object) -> None:
        now = time.monotonic()
        if now - self._last_warning_monotonic < _WARNING_COOLDOWN_SECONDS:
            logger.debug(message, *args)
            return
        self._last_warning_monotonic = now
        logger.warning(message, *args)


__all__ = ["BuzzFeedPoller"]
