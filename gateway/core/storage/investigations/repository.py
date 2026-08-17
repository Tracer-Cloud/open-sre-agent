"""Investigation records: the store contract and its process-local and Postgres implementations.

``InMemoryInvestigationRepository`` is process-local, so two gateway tasks would each
claim and run the same queued investigation. ``PostgresInvestigationRepository``
makes the queue a table and ``claim_next_queued`` takes the row with
``FOR UPDATE SKIP LOCKED``, so exactly one worker gets it — selected when
``DATABASE_URL`` is set (requires the ``postgresql`` extra); pooling and
connection bounds come from :class:`~gateway.core.storage.postgres.PostgresDatabase`.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from gateway.core.storage.investigations.tables import COLUMNS
from gateway.core.storage.postgres import PostgresDatabase


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class InvestigationRecord:
    id: str
    clerk_org_id: str
    status: InvestigationStatus
    trigger: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    workspace_id: str | None = None
    report_local_path: str | None = None
    report_s3_key: str | None = None
    error: str | None = None


class InvestigationRepository(Protocol):
    """Persistence contract shared by the in-memory and Postgres stores."""

    def create(
        self,
        *,
        clerk_org_id: str,
        trigger: dict[str, Any],
        workspace_id: str | None = None,
    ) -> InvestigationRecord:
        """Persist a new queued investigation and return it."""

    def get(self, investigation_id: str) -> InvestigationRecord | None:
        """Return the record for ``investigation_id``, or None when unknown."""

    def claim_next_queued(self) -> InvestigationRecord | None:
        """Atomically move the oldest queued record to running and return it."""

    def finish(
        self,
        investigation_id: str,
        *,
        status: InvestigationStatus,
        report_local_path: str | None = None,
        report_s3_key: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record the terminal status and artifact locations for a run."""

    def cancel(
        self,
        investigation_id: str,
        *,
        clerk_org_id: str,
    ) -> InvestigationRecord | None:
        """Cancel a queued investigation for ``clerk_org_id``, or None if not cancellable."""


@dataclass
class InMemoryInvestigationRepository:
    """Thread-safe process-local store (dev / single-instance default)."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _by_id: dict[str, InvestigationRecord] = field(default_factory=dict)

    def create(
        self,
        *,
        clerk_org_id: str,
        trigger: dict[str, Any],
        workspace_id: str | None = None,
    ) -> InvestigationRecord:
        now = datetime.now(UTC)
        record = InvestigationRecord(
            id=str(uuid.uuid4()),
            clerk_org_id=clerk_org_id,
            status=InvestigationStatus.QUEUED,
            trigger=trigger,
            created_at=now,
            updated_at=now,
            workspace_id=workspace_id,
        )
        with self._lock:
            self._by_id[record.id] = record
        return record

    def get(self, investigation_id: str) -> InvestigationRecord | None:
        with self._lock:
            record = self._by_id.get(investigation_id)
            return replace(record) if record else None

    def claim_next_queued(self) -> InvestigationRecord | None:
        with self._lock:
            queued = [
                record
                for record in self._by_id.values()
                if record.status is InvestigationStatus.QUEUED
            ]
            if not queued:
                return None
            record = min(queued, key=lambda r: r.created_at)
            record.status = InvestigationStatus.RUNNING
            record.updated_at = datetime.now(UTC)
            return replace(record)

    def finish(
        self,
        investigation_id: str,
        *,
        status: InvestigationStatus,
        report_local_path: str | None = None,
        report_s3_key: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = self._by_id.get(investigation_id)
            if record is None:
                return
            record.status = status
            record.report_local_path = report_local_path
            record.report_s3_key = report_s3_key
            record.error = error
            record.updated_at = datetime.now(UTC)

    def cancel(
        self,
        investigation_id: str,
        *,
        clerk_org_id: str,
    ) -> InvestigationRecord | None:
        with self._lock:
            record = self._by_id.get(investigation_id)
            if (
                record is None
                or record.clerk_org_id != clerk_org_id
                or record.status is not InvestigationStatus.QUEUED
            ):
                return None
            record.status = InvestigationStatus.CANCELLED
            record.updated_at = datetime.now(UTC)
            return replace(record)


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


class PostgresInvestigationRepository:
    """:class:`InvestigationRepository` on Postgres; safe for multiple workers via SKIP LOCKED."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    def create(
        self,
        *,
        clerk_org_id: str,
        trigger: dict[str, Any],
        workspace_id: str | None = None,
    ) -> InvestigationRecord:
        investigation_id = str(uuid.uuid4())
        with self._db.transaction() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO investigations
                    (id, clerk_org_id, workspace_id, status, trigger, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, now(), now())
                RETURNING {COLUMNS}
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
        with self._db.transaction() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {COLUMNS} FROM investigations WHERE id = %s",
                (investigation_id,),
            )
            row = cursor.fetchone()
            return _row_to_record(row) if row else None

    def claim_next_queued(self) -> InvestigationRecord | None:
        with self._db.transaction() as conn, conn.cursor() as cursor:
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
                RETURNING {COLUMNS}
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
        with self._db.transaction() as conn, conn.cursor() as cursor:
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
        with self._db.transaction() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE investigations
                SET status = %s, updated_at = now()
                WHERE id = %s AND clerk_org_id = %s AND status = %s
                RETURNING {COLUMNS}
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


def investigation_repository(database: PostgresDatabase | None) -> InvestigationRepository:
    """Postgres-backed when the process has a database, else process-local."""
    if database is None:
        return InMemoryInvestigationRepository()
    return PostgresInvestigationRepository(database)


__all__ = [
    "InMemoryInvestigationRepository",
    "InvestigationRecord",
    "InvestigationStatus",
    "InvestigationRepository",
    "PostgresInvestigationRepository",
    "investigation_repository",
]
