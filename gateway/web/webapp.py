"""The gateway's single FastAPI app: health probes, alert intake, investigations.

Every HTTP endpoint OpenSRE serves lives here, on one port — ``/`` ``/health``
``/ok`` (health probes), ``/healthz`` (liveness), ``POST /alerts`` (external
alert pushes into the process-wide :class:`AlertInbox`), and ``POST /investigate``
(run an investigation synchronously and return the RCA report). Hosted by the
gateway daemon and the interactive shell via :mod:`gateway.web.web_server`, or
standalone via ``uvicorn gateway.web.webapp:app``.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from bootstrap.process import WEB_PROFILE, configure_process
from config.environment import get_environment
from config.llm_settings import LLMSettings
from config.version import get_opensre_version
from core.agent_harness import AgentSession
from gateway.core.process.readiness import is_gateway_ready
from gateway.core.storage import open_database
from gateway.core.storage.investigations.repository import investigation_repository
from gateway.web.investigations import router as investigations_router
from infrastructure.alert_intake import require_local_or_token
from infrastructure.alert_intake import router as alert_router
from infrastructure.observability.errors.sentry import capture_exception
from infrastructure.process.turn_capacity import turn_slot
from infrastructure.request_body_limit import RequestBodyLimitMiddleware
from tools.investigation.capability import resolve_investigation_context, run_investigation_payload

# Standalone uvicorn and in-process gateway both need adapters for /investigate.
configure_process(WEB_PROFILE)  # env → sentry → adapters

logger = logging.getLogger(__name__)

__all__ = ["app"]


class HealthResponse(BaseModel):
    ok: bool
    version: str
    llm_configured: bool
    env: str


app = FastAPI()
# Above routing: every mutating route is bounded before FastAPI buffers a body.
app.add_middleware(RequestBodyLimitMiddleware)
app.state.investigations = investigation_repository(open_database())
app.include_router(investigations_router)
# Health liveness (/healthz) and alert intake (/alerts) live in the shared
# router so the interactive shell can serve them without importing the gateway.
app.include_router(alert_router)


def get_health_response() -> HealthResponse:
    try:
        LLMSettings.from_env()
        llm_configured = True
    except ValidationError:
        llm_configured = False

    return HealthResponse(
        ok=llm_configured,
        version=get_opensre_version(),
        llm_configured=llm_configured,
        env=get_environment().value,
    )


@app.get("/", response_model=HealthResponse)
@app.get("/health", response_model=HealthResponse)
@app.get("/ok", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    health_response = get_health_response()
    response.status_code = HTTPStatus.OK if health_response.ok else HTTPStatus.SERVICE_UNAVAILABLE
    return health_response


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Report mandatory startup readiness separately from process liveness."""
    if is_gateway_ready():
        return JSONResponse({"status": "ready"}, status_code=HTTPStatus.OK)
    return JSONResponse({"status": "not_ready"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE)


class InvestigateRequest(BaseModel):
    raw_alert: dict[str, Any]
    alert_name: str | None = None
    severity: str | None = None


class InvestigateResponse(BaseModel):
    report: str
    problem_md: str
    root_cause: str
    is_noise: bool = False
    validity_score: float = 0.0
    tool_calls: list[dict[str, Any]] | None = None


@app.post("/investigate", response_model=InvestigateResponse)
def investigate(req: InvestigateRequest, request: Request) -> InvestigateResponse | JSONResponse:
    """Run an investigation synchronously and return the RCA report.

    Lets external systems (CI pipelines, custom webhooks, chat integrations
    without a native tool) trigger the same investigation pipeline the CLI and
    interactive shell use, over HTTP. FastAPI runs this sync handler in a
    threadpool, so a long investigation does not block ``/health`` or ``/alerts``.
    """
    if (auth_error := require_local_or_token(request)) is not None:
        return auth_error

    from infrastructure.turn_host.concurrency import AT_CAPACITY_MESSAGE, process_turn_gate

    # Drop rather than queue: the caller is holding an HTTP connection open, so
    # it gets an answer now. Same gate chat and the scheduler take, same sentence
    # chat finalizes.
    with turn_slot(process_turn_gate()) as running:
        if not running:
            return JSONResponse(
                {"error": AT_CAPACITY_MESSAGE},
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        return _run_investigation(req)


def _run_investigation(req: InvestigateRequest) -> InvestigateResponse | JSONResponse:
    """Run one investigation and shape the response; capacity is the caller's."""
    investigation_metadata = resolve_investigation_context(
        raw_alert=req.raw_alert,
        alert_name=req.alert_name,
        severity=req.severity,
    )
    try:
        result = AgentSession().investigate(
            req.raw_alert,
            runner=run_investigation_payload,
            investigation_metadata=investigation_metadata,
        )
        return InvestigateResponse(**result.as_dict())
    except Exception as exc:
        # Full detail (which may include internal paths, stack context, or
        # upstream error bodies) goes to logs/Sentry only. The HTTP response
        # carries just the exception type so it stays actionable without
        # exposing internals to the caller (CodeQL: information exposure
        # through an exception).
        logger.exception("Investigation failed")
        capture_exception(exc, context="gateway.web.webapp.investigate")
        return JSONResponse(
            {"error": f"investigation failed: {type(exc).__name__}"},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )
