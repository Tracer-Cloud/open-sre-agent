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

from gateway.http.clerk_deps import ClerkClaims
from gateway.http.investigation_store import (
    InMemoryInvestigationStore,
    InvestigationStatus,
    InvestigationStore,
)
from gateway.http.worker import ensure_worker_started

router = APIRouter(prefix="/api/investigations", tags=["investigations"])

_store_lock = threading.Lock()
_store_instance: InvestigationStore | None = None


def _store() -> InvestigationStore:
    global _store_instance
    with _store_lock:
        if _store_instance is None:
            dsn = os.getenv("DATABASE_URL", "").strip()
            if dsn:
                from gateway.http.postgres_store import PostgresInvestigationStore

                _store_instance = PostgresInvestigationStore(dsn)
            else:
                _store_instance = InMemoryInvestigationStore()
        return _store_instance


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
    pipeline_name: str | None = None
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
        "pipeline_name": body.pipeline_name,
        "severity": body.severity,
    }
    record = store.create(
        clerk_org_id=clerk_org_id,
        trigger=trigger,
        workspace_id=body.workspace_id,
    )
    ensure_worker_started(store)
    return CreateInvestigationResponse(
        investigation_id=record.id,
        status=record.status,
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
