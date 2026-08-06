"""Async investigation API — enqueue now, poll later (ALB-safe).

Store selection: Postgres when ``DATABASE_URL`` is set, else process-local
memory. Do not widen the public response shape without a schema bump.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gateway.core.runtime.security_audit import audit_security_action
from gateway.core.storage.investigations.store import (
    InMemoryInvestigationStore,
    InvestigationRecord,
    InvestigationStatus,
    InvestigationStore,
)
from gateway.web.clerk_deps import ClerkClaims
from gateway.web.worker import ensure_worker_started

router = APIRouter(prefix="/api/investigations", tags=["investigations"])

_store_lock = threading.Lock()
_store_instance: InvestigationStore | None = None


def _store() -> InvestigationStore:
    global _store_instance
    with _store_lock:
        if _store_instance is None:
            dsn = os.getenv("DATABASE_URL", "").strip()
            if dsn:
                from gateway.core.storage.investigations.postgres import (
                    PostgresInvestigationStore,
                )

                _store_instance = PostgresInvestigationStore(dsn)
            else:
                _store_instance = InMemoryInvestigationStore()
        return _store_instance


def _audit_investigation(
    verb: str,
    record: InvestigationRecord,
    *,
    actor_id: str,
    clerk_org_id: str,
) -> None:
    """Record one investigation lifecycle action against the calling operator."""
    audit_security_action(
        action=f"investigation.{verb}",
        platform="http",
        actor_id=actor_id,
        resource_type="investigation",
        resource_id=record.id,
        outcome=record.status.value,
        detail={"clerk_org_id": clerk_org_id},
    )


def _require_org(claims_organization: str | None) -> str:
    """Return the caller's org id; reject org-less tokens.

    Without this, every user whose JWT carries no organization claim would
    share the empty-string namespace and could read each other's records.
    """
    org = (claims_organization or "").strip()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="organization membership required",
        )
    return org


class CreateInvestigationRequest(BaseModel):
    raw_alert: dict[str, Any] = Field(default_factory=dict)
    alert_name: str | None = None
    severity: str | None = None
    workspace_id: str | None = None


class CreateInvestigationResponse(BaseModel):
    investigation_id: str
    status: InvestigationStatus


class GetInvestigationResponse(BaseModel):
    investigation_id: str
    status: InvestigationStatus
    report_s3_key: str | None = None
    report_url: str | None = None
    error: str | None = None


@router.post(
    "",
    response_model=CreateInvestigationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_investigation(
    body: CreateInvestigationRequest,
    claims: ClerkClaims,
) -> CreateInvestigationResponse:
    """Enqueue an investigation; the background worker runs the pipeline."""
    store = _store()
    clerk_org_id = _require_org(claims.organization)
    trigger = {
        "raw_alert": body.raw_alert,
        "alert_name": body.alert_name,
        "severity": body.severity,
    }
    record = store.create(
        clerk_org_id=clerk_org_id,
        trigger=trigger,
        workspace_id=body.workspace_id,
    )
    _audit_investigation("create", record, actor_id=claims.sub, clerk_org_id=clerk_org_id)
    ensure_worker_started(store)
    return CreateInvestigationResponse(
        investigation_id=record.id,
        status=record.status,
    )


@router.post(
    "/{investigation_id}/cancel",
    response_model=GetInvestigationResponse,
)
def cancel_investigation(
    investigation_id: str,
    claims: ClerkClaims,
) -> GetInvestigationResponse | JSONResponse:
    """Cancel a queued investigation before the worker claims it."""
    clerk_org_id = _require_org(claims.organization)
    store = _store()
    record = store.get(investigation_id)
    if record is None or record.clerk_org_id != clerk_org_id:
        return JSONResponse({"error": "not found"}, status_code=status.HTTP_404_NOT_FOUND)
    cancelled = store.cancel(investigation_id, clerk_org_id=clerk_org_id)
    if cancelled is None:
        # Re-read so a concurrent claim/cancel does not return a stale status.
        current = store.get(investigation_id)
        current_status = current.status.value if current is not None else record.status.value
        return JSONResponse(
            {"error": "not cancellable", "status": current_status},
            status_code=status.HTTP_409_CONFLICT,
        )
    _audit_investigation("cancel", cancelled, actor_id=claims.sub, clerk_org_id=clerk_org_id)
    return GetInvestigationResponse(
        investigation_id=cancelled.id,
        status=cancelled.status,
        report_s3_key=cancelled.report_s3_key,
        report_url=None,
        error=cancelled.error,
    )


@router.get("/{investigation_id}", response_model=GetInvestigationResponse)
def get_investigation(
    investigation_id: str,
    claims: ClerkClaims,
) -> GetInvestigationResponse | JSONResponse:
    """Poll investigation status.

    ``report_s3_key`` is the durable artifact reference; ``report_url`` is
    reserved for presigned downloads and stays null until URL generation lands.
    """
    clerk_org_id = _require_org(claims.organization)
    record = _store().get(investigation_id)
    if record is None:
        return JSONResponse({"error": "not found"}, status_code=status.HTTP_404_NOT_FOUND)
    if record.clerk_org_id != clerk_org_id:
        return JSONResponse({"error": "not found"}, status_code=status.HTTP_404_NOT_FOUND)
    return GetInvestigationResponse(
        investigation_id=record.id,
        status=record.status,
        report_s3_key=record.report_s3_key,
        report_url=None,
        error=record.error,
    )
