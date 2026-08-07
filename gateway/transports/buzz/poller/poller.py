"""Poll ``buzz feed get`` for mentions and yield normalized inbound messages."""

from __future__ import annotations

import logging
import threading
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
    with ``created_at >= since`` match), so the cursor cannot simply track the
    newest event seen — re-polling would replay it forever. It instead names
    the oldest second still worth asking for, with ``_acked_at_cursor`` deduping
    within that one second rather than advancing past it, which would risk
    skipping a different event sharing the same timestamp.

    **The cursor tracks handled work, not fetched work.** ``poll_once`` marks
    what it returns as in flight; only :meth:`acknowledge` — called once a turn
    has actually run — lets the cursor move, and only past events strictly
    older than the oldest turn still running. A turn that dies mid-flight, or
    that shutdown cuts short, therefore leaves the cursor behind it and is
    re-delivered on the next start instead of being silently skipped.

    Both the watermark and the inclusive-second ID set are persisted so a
    restart does not re-dispatch work that already completed.
    """

    def __init__(self, client: BuzzClient) -> None:
        self._client = client
        self._lock = threading.Lock()
        state = load_cursor()
        self._since = state.since
        # Ids at exactly ``_since`` that are fully handled — the inclusive-since
        # dedup set. Persisted with the watermark so a restart does not replay
        # completed work at the inclusive boundary.
        self._acked_at_cursor: set[str] = set(state.acked_ids)
        # event_id -> created_at for events dispatched but not yet handled.
        self._inflight: dict[str, int] = {}
        # Handled events the cursor cannot cover yet because an older turn is
        # still running. Bounded by in-flight concurrency, not by feed volume.
        self._acked_ahead: dict[str, int] = {}
        self._last_warning_monotonic = 0.0

    def poll_once(self) -> list[BuzzInboundMessage]:
        """Fetch events not yet dispatched, and mark them in flight."""
        result = self._client.get_feed(since=self._since, types=_FEED_TYPES)
        if not result["success"]:
            self._log_transient("[buzz-gateway] feed get failed: %s", result["error"])
            return []

        fresh: list[BuzzInboundMessage] = []
        with self._lock:
            for raw in result["events"]:
                if not isinstance(raw, dict):
                    continue
                parsed = parse_feed_event(raw)
                if parsed is None or self._already_seen(parsed):
                    continue
                self._inflight[parsed.event_id] = parsed.created_at
                fresh.append(parsed)
        return fresh

    def acknowledge(self, event: BuzzInboundMessage) -> None:
        """Mark one dispatched event handled and advance the cursor if safe."""
        with self._lock:
            created_at = self._inflight.pop(event.event_id, None)
            if created_at is None:
                return
            self._acked_ahead[event.event_id] = created_at
            self._advance_cursor()

    def inflight_count(self) -> int:
        """How many dispatched events have not been acknowledged yet."""
        with self._lock:
            return len(self._inflight)

    def _already_seen(self, event: BuzzInboundMessage) -> bool:
        if event.event_id in self._inflight or event.event_id in self._acked_ahead:
            return True
        return event.created_at == self._since and event.event_id in self._acked_at_cursor

    def _advance_cursor(self) -> None:
        """Move the cursor to the newest handled second no in-flight turn needs."""
        oldest_inflight = min(self._inflight.values(), default=None)
        coverable = [
            created_at
            for created_at in self._acked_ahead.values()
            # Strictly older: ``since`` has one-second resolution, so covering
            # the same second as a running turn would skip it on restart.
            if oldest_inflight is None or created_at < oldest_inflight
        ]
        if not coverable:
            return
        watermark = max(coverable)
        if watermark < self._since:
            return
        if watermark > self._since:
            self._since = watermark
            self._acked_at_cursor = set()
        self._acked_at_cursor.update(
            event_id
            for event_id, created_at in self._acked_ahead.items()
            if created_at == self._since
        )
        self._acked_ahead = {
            event_id: created_at
            for event_id, created_at in self._acked_ahead.items()
            if created_at > self._since
        }
        # Persist after the ID set is updated — a restart must not lose the
        # inclusive-second dedup and re-run completed turns.
        save_cursor(self._since, self._acked_at_cursor)

    def _log_transient(self, message: str, *args: object) -> None:
        now = time.monotonic()
        if now - self._last_warning_monotonic < _WARNING_COOLDOWN_SECONDS:
            logger.debug(message, *args)
            return
        self._last_warning_monotonic = now
        logger.warning(message, *args)


__all__ = ["BuzzFeedPoller"]
