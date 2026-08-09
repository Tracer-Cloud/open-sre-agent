"""Investigation store on Postgres, for deployments running several workers.

``InMemoryInvestigationStore`` is process-local, so two gateway tasks would each
claim and run the same queued investigation. Here the queue is a table and
``claim_next_queued`` takes the row with ``FOR UPDATE SKIP LOCKED``, so exactly
one worker gets it.

Selected when ``DATABASE_URL`` is set; requires the ``postgresql`` extra.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from gateway.core.storage.investigations.store import InvestigationRecord, InvestigationStatus

_POOL_MIN_CONNECTIONS = 1
# Bounds concurrent server connections: the worker plus a burst of API threads.
_POOL_MAX_CONNECTIONS = 10

# psycopg2 passes connect_timeout straight through to libpq,
# which waits forever when connect_timeout is unset or 0,
# so a blackholed address would hang store
# construction under the module lock in gateway/web/investigations.py.
# Operators may tune this through DATABASE_URL;e.g. ``postgresql://....?connect_timeout=10``.
_CONNECT_TIMEOUT_SECONDS = 10  # default connect_timeout applied to the pool DSN
_CONNECT_TIMEOUT_MAX_SECONDS = 30  # ceiling; caps an operator-supplied DATABASE_URL value

# Keepalives detect a silently dropped connection;
# connect_timeout covers only the handshake.

_KEEPALIVES_IDLE_SECONDS = 30
_KEEPALIVES_INTERVAL_SECONDS = 10
_KEEPALIVES_COUNT = 3

_COLUMNS = (
    "id, clerk_org_id, workspace_id, status, trigger, error, "
    "report_local_path, report_s3_key, created_at, updated_at"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    clerk_org_id TEXT NOT NULL,
    workspace_id TEXT,
    status TEXT NOT NULL,
    trigger JSONB NOT NULL,
    error TEXT,
    report_local_path TEXT,
    report_s3_key TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS report_local_path TEXT;
CREATE INDEX IF NOT EXISTS investigations_status_created
    ON investigations (status, created_at);
"""


def _bounded_connect_timeout(requested: str | None) -> int:
    """Clamp an operator-supplied ``connect_timeout`` into the bound we enforce."""
    try:
        seconds = int(requested or 0)
    except ValueError:
        seconds = 0
    if seconds <= 0:  # unset, unparseable, or libpq's "wait forever"
        return _CONNECT_TIMEOUT_SECONDS  # default
    return min(seconds, _CONNECT_TIMEOUT_MAX_SECONDS)


def _connect_kwargs(dsn: str) -> dict[str, Any]:
    """Bounds applied to every connection the pool opens; these override the DSN."""
    # Local import: the postgresql extra is optional.
    from psycopg2.extensions import parse_dsn

    return {
        "connect_timeout": _bounded_connect_timeout(parse_dsn(dsn).get("connect_timeout")),
        "keepalives": 1,
        "keepalives_idle": _KEEPALIVES_IDLE_SECONDS,
        "keepalives_interval": _KEEPALIVES_INTERVAL_SECONDS,
        "keepalives_count": _KEEPALIVES_COUNT,
    }


def _row_to_record(row: tuple[Any, ...]) -> InvestigationRecord:
    trigger = row[4]
    if isinstance(trigger, str):
        trigger = json.loads(trigger)
    return InvestigationRecord(
        id=row[0],
        clerk_org_id=row[1],
        workspace_id=row[2],
        status=InvestigationStatus(row[3]),
        trigger=trigger,
        error=row[5],
        report_local_path=row[6],
        report_s3_key=row[7],
        created_at=row[8],
        updated_at=row[9],
    )


class PostgresInvestigationStore:
    """:class:`InvestigationStore` on Postgres; safe for multiple workers via SKIP LOCKED."""

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
                    _POOL_MIN_CONNECTIONS,
                    _POOL_MAX_CONNECTIONS,
                    self._dsn,
                    **_connect_kwargs(self._dsn),
                )
            return self._pool

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        """Yield a pooled connection; commit on success, roll back on error, always return it."""
        pool = self._get_pool()
        conn = pool.getconn()
        try:
            with conn:
                yield conn
        finally:
            pool.putconn(conn)

    def create(
        self,
        *,
        clerk_org_id: str,
        trigger: dict[str, Any],
        workspace_id: str | None = None,
    ) -> InvestigationRecord:
        investigation_id = str(uuid.uuid4())
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO investigations
                    (id, clerk_org_id, workspace_id, status, trigger, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, now(), now())
                RETURNING {_COLUMNS}
                """,
                (
                    investigation_id,
                    clerk_org_id,
                    workspace_id,
                    InvestigationStatus.QUEUED.value,
                    json.dumps(trigger),
                ),
            )
            return _row_to_record(cursor.fetchone())

    def get(self, investigation_id: str) -> InvestigationRecord | None:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM investigations WHERE id = %s",
                (investigation_id,),
            )
            row = cursor.fetchone()
            return _row_to_record(row) if row else None

    def claim_next_queued(self) -> InvestigationRecord | None:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE investigations
                SET status = %s, updated_at = now()
                WHERE id = (
                    SELECT id FROM investigations
                    WHERE status = %s
                    ORDER BY created_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING {_COLUMNS}
                """,
                (InvestigationStatus.RUNNING.value, InvestigationStatus.QUEUED.value),
            )
            row = cursor.fetchone()
            return _row_to_record(row) if row else None

    def finish(
        self,
        investigation_id: str,
        *,
        status: InvestigationStatus,
        report_local_path: str | None = None,
        report_s3_key: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE investigations
                SET status = %s, report_local_path = %s, report_s3_key = %s,
                    error = %s, updated_at = now()
                WHERE id = %s
                """,
                (status.value, report_local_path, report_s3_key, error, investigation_id),
            )

    def cancel(
        self,
        investigation_id: str,
        *,
        clerk_org_id: str,
    ) -> InvestigationRecord | None:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE investigations
                SET status = %s, updated_at = now()
                WHERE id = %s AND clerk_org_id = %s AND status = %s
                RETURNING {_COLUMNS}
                """,
                (
                    InvestigationStatus.CANCELLED.value,
                    investigation_id,
                    clerk_org_id,
                    InvestigationStatus.QUEUED.value,
                ),
            )
            row = cursor.fetchone()
            return _row_to_record(row) if row else None
