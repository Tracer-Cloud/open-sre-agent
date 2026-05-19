"""HTTP entrypoint: alert ingestion endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["alerts"])


class AlertIngestRequest(BaseModel):
    alert_payload: dict[str, Any] = Field(..., description="Raw alert payload to ingest")
    source: str | None = Field(default=None, description="Alert source (e.g. datadog, grafana)")


class AlertIngestResponse(BaseModel):
    ok: bool
    message: str = "Alert received"


@router.post("/alerts", response_model=AlertIngestResponse)
def ingest_alert(req: AlertIngestRequest) -> AlertIngestResponse:
    """Ingest an alert payload for async investigation processing."""
    # Alert queuing / async dispatch will be wired in HEA-16.
    # For now, acknowledge receipt and let the caller decide whether to
    # fire a synchronous investigation via POST /api/v1/investigate.
    return AlertIngestResponse(ok=True, message="Alert received")
