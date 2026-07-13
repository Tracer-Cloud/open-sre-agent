"""Postgres-backed investigation store (requires the ``postgresql`` extra)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from gateway.api.investigation_store import InvestigationRecord, InvestigationStatus

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
CREATE INDEX IF NOT EXISTS investigations_status_created
    ON investigations (status, created_at);
"""


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
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(_SCHEMA)

    def _connect(self) -> Any:
        import psycopg2  # local import: the postgresql extra is optional

        return psycopg2.connect(self._dsn)

    def create(
        self,
        *,
        clerk_org_id: str,
        trigger: dict[str, Any],
        workspace_id: str | None = None,
    ) -> InvestigationRecord:
        investigation_id = str(uuid.uuid4())
        with self._connect() as conn, conn.cursor() as cursor:
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
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM investigations WHERE id = %s",
                (investigation_id,),
            )
            row = cursor.fetchone()
            return _row_to_record(row) if row else None

    def claim_next_queued(self) -> InvestigationRecord | None:
        with self._connect() as conn, conn.cursor() as cursor:
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
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE investigations
                SET status = %s, report_local_path = %s, report_s3_key = %s,
                    error = %s, updated_at = now()
                WHERE id = %s
                """,
                (status.value, report_local_path, report_s3_key, error, investigation_id),
            )
