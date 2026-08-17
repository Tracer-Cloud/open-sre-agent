"""Cross-replica Slack event de-duplication on Postgres.

Slack delivers at least once: a retry follows any non-2xx, timeout, or replica
that dies mid-request. With more than one HTTP replica the retry lands wherever
the load balancer sends it, so the "already handled" set has to outlive the
process — a per-process set admits the retry again and runs the turn twice,
posting a second reply and repeating every tool action the turn performed.

Claims are provisional until the turn is queued. A primary-key insert wins the
race; ``confirm`` marks it final after ``submit_turn`` succeeds. If the
executor rejects the work and ``release`` cannot delete the row, a later
``claim`` may reclaim an *uncommitted* row so Slack's retry is not answered
200-as-duplicate and permanently dropped. Committed rows are never reclaimed.

Selected when ``DATABASE_URL`` is set; requires the ``postgresql`` extra.
"""

from __future__ import annotations

import logging

from gateway.core.storage.postgres import PostgresDatabase

logger = logging.getLogger("gateway")


# Slack stops retrying an event well inside this window, so a row older than
# it can never match a live delivery. Kept as an interval rather than a sweep
# job: the delete runs with the insert and touches only expired rows.
RETENTION_MINUTES = 60

# An uncommitted row means "a delivery took this and has not confirmed yet".
# Younger than this it is assumed to be in flight, so a concurrent duplicate is
# refused; older it is assumed abandoned (release could not reach the database,
# or the replica died between claim and confirm) and may be reclaimed. Must stay
# well under Slack's retry interval so a genuine retry can still get through,
# and comfortably above the claim→submit gap, which is a queue push.
ABANDONED_CLAIM_SECONDS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_handled_events (
    event_id TEXT PRIMARY KEY,
    handled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS slack_handled_events_handled_at_idx
    ON slack_handled_events (handled_at);
"""

# Older deploys created the table without ``committed``. Default TRUE so
# pre-existing rows stay refused (they already ran or were treated as handled).
_MIGRATE_COMMITTED = """
ALTER TABLE slack_handled_events
    ADD COLUMN IF NOT EXISTS committed BOOLEAN NOT NULL DEFAULT TRUE;
"""


class PostgresSlackEventDeduplicator:
    """:class:`SlackEventDeduplicator` shared by every gateway replica."""

    def __init__(self, dsn: str, *, database: PostgresDatabase | None = None) -> None:
        self._db = database if database is not None else PostgresDatabase(dsn)
        self._db.run_schema(_SCHEMA, _MIGRATE_COMMITTED)

    def release(self, event_id: str) -> bool:
        """Drop a provisional claim whose turn never started.

        Without this, a delivery admitted but not dispatched (executor gone) is
        refused on retry and the user's message is silently lost. Returns False
        when the store could not delete the row — callers should not pretend the
        retry path is clean (see route: 500 vs 503). Uncommitted rows may still
        be reclaimed by a later ``claim``.
        """
        try:
            with self._db.transaction() as conn, conn.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM slack_handled_events
                    WHERE event_id = %s AND committed IS FALSE
                    """,
                    (event_id,),
                )
            return True
        except Exception:
            logger.error(
                "[slack-gateway] could not release event claim %s; a retry may "
                "reclaim the provisional row or be refused if already committed",
                event_id,
                exc_info=True,
            )
            return False

    def confirm(self, event_id: str) -> bool:
        """Mark a provisional claim final after the turn has been queued."""
        try:
            with self._db.transaction() as conn, conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE slack_handled_events
                    SET committed = TRUE, handled_at = now()
                    WHERE event_id = %s
                    """,
                    (event_id,),
                )
            return True
        except Exception:
            logger.error(
                "[slack-gateway] could not confirm event claim %s; a retry may "
                "reclaim and start a second turn",
                event_id,
                exc_info=True,
            )
            return False

    def claim(self, event_id: str) -> bool:
        """Return True when this delivery may run the turn.

        First insert wins. A committed row always refuses. An uncommitted row
        is refused while it is younger than ``ABANDONED_CLAIM_SECONDS`` — it
        means a delivery is mid-flight, and reclaiming it would queue the same
        turn twice — and reclaimed once older, which recovers a claim whose
        release never reached the database. A database failure returns True:
        dropping a real user message is worse than a possible duplicate.
        """
        try:
            with self._db.transaction() as conn, conn.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM slack_handled_events
                    WHERE handled_at < now() - %s::interval
                    """,
                    (f"{RETENTION_MINUTES} minutes",),
                )
                cursor.execute(
                    """
                    INSERT INTO slack_handled_events (event_id, committed)
                    VALUES (%s, FALSE)
                    ON CONFLICT (event_id) DO UPDATE
                    SET handled_at = now()
                    WHERE slack_handled_events.committed IS FALSE
                      AND slack_handled_events.handled_at
                          < now() - %s::interval
                    RETURNING event_id
                    """,
                    (event_id, f"{ABANDONED_CLAIM_SECONDS} seconds"),
                )
                return cursor.fetchone() is not None
        except Exception:
            logger.warning(
                "[slack-gateway] event dedup unavailable; admitting delivery", exc_info=True
            )
            return True


__all__ = [
    "ABANDONED_CLAIM_SECONDS",
    "RETENTION_MINUTES",
    "PostgresSlackEventDeduplicator",
]
