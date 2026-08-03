"""The agent-run queue on Postgres, for gateway workers claiming remote runs.

Implements the three :class:`AgentRunRepository` methods a gateway worker uses:
claim the oldest available run, keep its lease alive while it works, and record
the terminal result. The remaining control-plane tables (tenant credentials,
container lifecycle) belong to the infrastructure repo and are not modelled here.

The queue semantics live in SQL rather than in Python: ``FOR UPDATE SKIP LOCKED``
lets several workers claim concurrently without handing the same run to two of
them, and every write re-checks ``claimed_by`` and the lease so a worker whose
lease has expired cannot finalize a run another worker has since taken.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from platform.deployment_contracts.models import AgentRun, AgentRunSource, AgentRunStatus

#: Column order every row mapper below depends on.
_RUN_COLUMNS = (
    "id, organization_id, source, source_event_id, prompt, status, result, "
    "error_code, claimed_by, lease_expires_at, attempt_count, created_at, updated_at"
)
_POOL_MIN_CONNECTIONS = 1
_POOL_MAX_CONNECTIONS = 4
_CONNECT_TIMEOUT_SECONDS = 10


def _lease_seconds(lease_duration: timedelta) -> float:
    seconds = lease_duration.total_seconds()
    if seconds <= 0:
        raise ValueError("lease_duration must be positive")
    return seconds


def _optional_result(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object in agent run result")
    return parsed


def _to_agent_run(row: tuple[Any, ...]) -> AgentRun:
    return AgentRun(
        id=row[0],
        organization_id=row[1],
        source=AgentRunSource(row[2]),
        source_event_id=row[3],
        prompt=row[4],
        status=AgentRunStatus(row[5]),
        result=_optional_result(row[6]),
        error_code=row[7],
        claimed_by=row[8],
        lease_expires_at=row[9],
        attempt_count=row[10],
        created_at=row[11],
        updated_at=row[12],
    )


class PostgresAgentRunRepository:
    """:class:`AgentRunRepository` on Postgres, safe for several gateway workers."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None
        self._pool_lock = threading.Lock()

    def _get_pool(self) -> Any:
        with self._pool_lock:
            if self._pool is None:
                from psycopg2.pool import ThreadedConnectionPool

                self._pool = ThreadedConnectionPool(
                    _POOL_MIN_CONNECTIONS,
                    _POOL_MAX_CONNECTIONS,
                    self._dsn,
                    connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                )
            return self._pool

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        """Borrow a pooled connection and always return it."""
        pool = self._get_pool()
        connection = pool.getconn()
        try:
            with connection:
                yield connection
        finally:
            pool.putconn(connection)

    def claim_oldest_available_agent_run(
        self,
        *,
        organization_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> AgentRun | None:
        """Take the oldest queued run, or one whose previous worker let the lease lapse."""
        seconds = _lease_seconds(lease_duration)
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE agent_runs
                SET status = %s,
                    claimed_by = %s,
                    lease_expires_at = now() + make_interval(secs => %s),
                    attempt_count = attempt_count + 1,
                    updated_at = now()
                WHERE id = (
                    SELECT id
                    FROM agent_runs
                    WHERE organization_id = %s
                      AND (
                          status = %s
                          OR (status = %s AND lease_expires_at < now())
                      )
                    ORDER BY created_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING {_RUN_COLUMNS}
                """,  # noqa: S608
                (
                    AgentRunStatus.RUNNING.value,
                    worker_id,
                    seconds,
                    organization_id,
                    AgentRunStatus.QUEUED.value,
                    AgentRunStatus.RUNNING.value,
                ),
            )
            row = cursor.fetchone()
            return _to_agent_run(row) if row else None

    def extend_owned_agent_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> bool:
        """Push this worker's lease out; False once someone else owns the run."""
        seconds = _lease_seconds(lease_duration)
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_runs
                SET lease_expires_at = now() + make_interval(secs => %s),
                    updated_at = now()
                WHERE id = %s
                  AND claimed_by = %s
                  AND status = %s
                  AND lease_expires_at > now()
                """,
                (seconds, run_id, worker_id, AgentRunStatus.RUNNING.value),
            )
            return int(cursor.rowcount) == 1

    def finalize_owned_agent_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        status: AgentRunStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        """Record the terminal result, only while this worker still holds the lease."""
        if status not in {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED}:
            raise ValueError("finalize_owned_agent_run requires a terminal status")
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_runs
                SET status = %s,
                    result = %s::jsonb,
                    error_code = %s,
                    claimed_by = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = %s
                  AND claimed_by = %s
                  AND status = %s
                  AND lease_expires_at > now()
                """,
                (
                    status.value,
                    json.dumps(result) if result is not None else None,
                    error_code,
                    run_id,
                    worker_id,
                    AgentRunStatus.RUNNING.value,
                ),
            )
            return int(cursor.rowcount) == 1


__all__ = ["PostgresAgentRunRepository"]
