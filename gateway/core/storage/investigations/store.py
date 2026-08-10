"""Investigation records and store contract for the async investigation API."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvestigationOrigin(StrEnum):
    REST = "rest"
    CHAT = "chat"


@dataclass
class InvestigationRecord:
    id: str
    clerk_org_id: str
    status: InvestigationStatus
    trigger: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    origin: InvestigationOrigin
    workspace_id: str | None = None
    report_local_path: str | None = None
    report_s3_key: str | None = None
    error: str | None = None


class InvestigationStore(Protocol):
    """Persistence contract shared by the in-memory and Postgres stores."""

    def create(
        self,
        *,
        clerk_org_id: str,
        trigger: dict[str, Any],
        origin: InvestigationOrigin,
        workspace_id: str | None = None,
    ) -> InvestigationRecord:
        """Persist a new queued investigation and return it."""

    def get(self, investigation_id: str) -> InvestigationRecord | None:
        """Return the record for ``investigation_id``, or None when unknown."""

    def claim_next_queued(self, *, origin: InvestigationOrigin) -> InvestigationRecord | None:
        """Atomically move the oldest queued record with matching origin to running and return it."""

    def claim(self, investigation_id: str) -> InvestigationRecord | None:
        """Atomically move one named queued record to running, or None if it was not queued.

        The in-process fallback already knows which record it launched, so it
        claims by id rather than racing the queue. ``None`` means somebody else
        got there first — the caller must not run the pipeline a second time.
        """

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

    def touch(self, investigation_id: str) -> None:
        """Update the heartbeat timestamp for a running investigation."""


@dataclass
class InMemoryInvestigationStore:
    """Thread-safe process-local store (dev / single-instance default)."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _by_id: dict[str, InvestigationRecord] = field(default_factory=dict)

    def create(
        self,
        *,
        clerk_org_id: str,
        trigger: dict[str, Any],
        origin: InvestigationOrigin,
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
            origin=origin,
            workspace_id=workspace_id,
        )
        with self._lock:
            self._by_id[record.id] = record
        return record

    def get(self, investigation_id: str) -> InvestigationRecord | None:
        with self._lock:
            record = self._by_id.get(investigation_id)
            return replace(record) if record else None

    def claim_next_queued(self, *, origin: InvestigationOrigin) -> InvestigationRecord | None:
        with self._lock:
            queued = [
                record
                for record in self._by_id.values()
                if record.status is InvestigationStatus.QUEUED and record.origin is origin
            ]
            if not queued:
                return None
            record = min(queued, key=lambda r: r.created_at)
            record.status = InvestigationStatus.RUNNING
            record.updated_at = datetime.now(UTC)
            return replace(record)

    def claim(self, investigation_id: str) -> InvestigationRecord | None:
        with self._lock:
            record = self._by_id.get(investigation_id)
            if record is None or record.status is not InvestigationStatus.QUEUED:
                return None
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

    def touch(self, investigation_id: str) -> None:
        """Update the heartbeat timestamp for a running investigation."""
        with self._lock:
            record = self._by_id.get(investigation_id)
            if record is not None:
                record.updated_at = datetime.now(UTC)
