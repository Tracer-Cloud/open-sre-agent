"""Cross-replica Slack event de-duplication on Postgres.

Slack delivers at least once: a retry follows any non-2xx, timeout, or replica
that dies mid-request. With more than one HTTP replica the retry lands wherever
the load balancer sends it, so the "already handled" set has to outlive the
process — a per-process set admits the retry again and runs the turn twice,
posting a second reply and repeating every tool action the turn performed.

Exactly-once falls out of a primary-key conflict: the first insert of an
``event_id`` wins, every retry conflicts and is refused. No lock, no read
before write, and correct regardless of which replica sees which delivery.

Selected when ``DATABASE_URL`` is set; requires the ``postgresql`` extra.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("gateway")

_POOL_MIN_CONNECTIONS = 1
# Admission is one short statement per request; a small pool covers the
# listener's concurrency without holding server connections open needlessly.
_POOL_MAX_CONNECTIONS = 5

# Slack stops retrying an event well inside this window, so a row older than
# it can never match a live delivery. Kept as an interval rather than a sweep
# job: the delete runs with the insert and touches only expired rows.
RETENTION_MINUTES = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_handled_events (
    event_id TEXT PRIMARY KEY,
    handled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS slack_handled_events_handled_at_idx
    ON slack_handled_events (handled_at);
"""


class PostgresSlackEventDeduplicator:
    """:class:`SlackEventDeduplicator` shared by every gateway replica."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None
        self._pool_lock = threading.Lock()
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(_SCHEMA)

    def _get_pool(self) -> Any:
        with self._pool_lock:
            if self._pool is None:
                # Local import: the postgresql extra is optional.
                from psycopg2.pool import ThreadedConnectionPool

                self._pool = ThreadedConnectionPool(
                    _POOL_MIN_CONNECTIONS, _POOL_MAX_CONNECTIONS, self._dsn
                )
            return self._pool

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        """Yield a pooled connection; commit on success, roll back on error."""
        pool = self._get_pool()
        conn = pool.getconn()
        try:
            with conn:
                yield conn
        finally:
            pool.putconn(conn)

    def claim(self, event_id: str) -> bool:
        """Return True only for the first delivery of ``event_id``.

        A database failure returns True — the turn runs. Dropping a real user
        message to avoid a possible duplicate is the worse of the two, and the
        failure is logged rather than silently swallowed.
        """
        try:
            with self._connection() as conn, conn.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM slack_handled_events
                    WHERE handled_at < now() - %s::interval
                    """,
                    (f"{RETENTION_MINUTES} minutes",),
                )
                cursor.execute(
                    """
                    INSERT INTO slack_handled_events (event_id)
                    VALUES (%s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (event_id,),
                )
                return bool(cursor.rowcount)
        except Exception:
            logger.warning(
                "[slack-gateway] event dedup unavailable; admitting delivery", exc_info=True
            )
            return True


__all__ = ["RETENTION_MINUTES", "PostgresSlackEventDeduplicator"]
