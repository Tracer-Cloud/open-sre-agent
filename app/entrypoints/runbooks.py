"""Runbook management and semantic search endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.runbook_service import RunbookService

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/runbooks", tags=["runbooks"])


class RunbookCreate(BaseModel):
    title: str = Field(..., max_length=500)
    content: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    source_url: str | None = Field(default=None, max_length=2000)


class RunbookResponse(BaseModel):
    id: str
    tenant_id: str
    title: str
    content: str
    tags: list[str]
    source_url: str | None

    model_config = {"from_attributes": True}


@router.post(
    "",
    response_model=RunbookResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Store or update a runbook and generate its embedding",
)
def upsert_runbook(
    tenant_id: str,
    body: RunbookCreate,
    db: Annotated[Session, Depends(get_db)],
) -> RunbookResponse:
    svc = RunbookService(db)
    runbook = svc.upsert(
        tenant_id=tenant_id,
        title=body.title,
        content=body.content,
        tags=body.tags,
        source_url=body.source_url,
    )
    db.commit()
    return RunbookResponse.model_validate(runbook)


@router.get(
    "/search",
    response_model=list[RunbookResponse],
    summary="Semantic search over a tenant's runbooks",
)
def search_runbooks(
    tenant_id: str,
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, description="Natural-language query")] = "",
    top_k: Annotated[int, Query(ge=1, le=20)] = 3,
) -> list[RunbookResponse]:
    svc = RunbookService(db)
    results = svc.search(tenant_id=tenant_id, query=q, top_k=top_k)
    return [RunbookResponse.model_validate(r) for r in results]


@router.delete(
    "/{runbook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a runbook",
)
def delete_runbook(
    tenant_id: str,
    runbook_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    svc = RunbookService(db)
    found = svc.delete(tenant_id=tenant_id, runbook_id=runbook_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runbook not found")
    db.commit()
