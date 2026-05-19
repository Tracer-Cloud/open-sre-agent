"""HTTP entrypoint: investigation trigger endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["investigations"])


class InvestigateRequest(BaseModel):
    alert_payload: dict[str, Any] = Field(..., description="Raw alert payload")
    alert_name: str | None = Field(default=None, description="Optional alert name override")
    pipeline_name: str | None = Field(default=None, description="Optional pipeline name override")
    severity: str | None = Field(default=None, description="Optional severity override")


class InvestigateResponse(BaseModel):
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None


@router.post("/investigate", response_model=InvestigateResponse)
def trigger_investigation(req: InvestigateRequest) -> InvestigateResponse:
    """Trigger an RCA investigation for the given alert payload."""
    from app.entrypoints.sdk import run_investigation

    payload: dict[str, Any] = dict(req.alert_payload)
    common_labels = dict(payload.get("commonLabels", {}))
    if req.alert_name:
        common_labels["alertname"] = req.alert_name
    if req.pipeline_name:
        common_labels["pipeline_name"] = req.pipeline_name
    if req.severity:
        common_labels["severity"] = req.severity
    payload["commonLabels"] = common_labels

    try:
        state = run_investigation(payload)
        return InvestigateResponse(ok=True, result=dict(state))
    except Exception as exc:
        return InvestigateResponse(ok=False, error=str(exc), error_type=type(exc).__name__)
