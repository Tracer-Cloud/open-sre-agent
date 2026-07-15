"""The gateway's single FastAPI app: health probes, alert intake, investigations.

Every HTTP endpoint OpenSRE serves lives here, on one port — ``/`` ``/health``
``/ok`` (health probes), ``/healthz`` (liveness), ``POST /alerts`` (external
alert pushes into the process-wide :class:`AlertInbox`), and ``POST /investigate``
(run an investigation synchronously and return the RCA report). Hosted by the
gateway daemon and the interactive shell via :mod:`gateway.http.web_server`, or
standalone via ``uvicorn gateway.http.webapp:app``.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from config.config import LLMSettings, get_environment
from config.platform_bootstrap import ensure_project_platform_package
from config.version import get_opensre_version
from core.domain.alerts.inbox import (
    AlertInbox,
    IncomingAlert,
    get_current_inbox,
    set_current_inbox,
)

ensure_project_platform_package()

from gateway.http.investigations import router as investigations_router  # noqa: E402
from platform.observability.errors.sentry import capture_exception, init_sentry  # noqa: E402
from tools.investigation.capability import (  # noqa: E402
    resolve_investigation_context,
    run_investigation_payload,
)

init_sentry(entrypoint="webapp")

logger = logging.getLogger(__name__)

# Cap on POST body size accepted from any caller (authed or not). Realistic
# alert payloads top out around 50 KB, so 1 MiB is ~20× headroom.
MAX_ALERT_BODY_BYTES = 1 * 1024 * 1024

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class HealthResponse(BaseModel):
    ok: bool
    version: str
    llm_configured: bool
    env: str


app = FastAPI()
app.include_router(investigations_router)


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
    response.status_code = (
        status.HTTP_200_OK if health_response.ok else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return health_response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _alert_inbox() -> AlertInbox:
    """The process-wide inbox; hosts may install their own via set_current_inbox."""
    inbox = get_current_inbox()
    if inbox is None:
        inbox = AlertInbox()
        set_current_inbox(inbox)
    return inbox


def _gateway_auth_error(request: Request) -> JSONResponse | None:
    """Bearer-token auth when configured; otherwise loopback callers only.

    Shared by every mutating gateway route (``/alerts``, ``/investigate``) since
    they sit behind the same trust boundary: local callers or a configured token.
    """
    token = os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN")
    if token:
        supplied = request.headers.get("authorization", "")
        if hmac.compare_digest(supplied, f"Bearer {token}"):
            return None
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    client_host = request.client.host if request.client else ""
    if client_host in _LOOPBACK_HOSTS:
        return None
    return JSONResponse(
        {"error": "set OPENSRE_ALERT_LISTENER_TOKEN to accept non-loopback callers"},
        status_code=403,
    )


@app.post("/alerts")
async def receive_alert(request: Request) -> JSONResponse:
    if (auth_error := _gateway_auth_error(request)) is not None:
        return auth_error

    try:
        declared_length = int(request.headers.get("content-length", 0))
    except ValueError:
        return JSONResponse({"error": "invalid Content-Length"}, status_code=400)
    if declared_length < 0:
        return JSONResponse({"error": "invalid Content-Length"}, status_code=400)
    if declared_length > MAX_ALERT_BODY_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)

    body = await request.body()
    if len(body) > MAX_ALERT_BODY_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)

    try:
        data = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    try:
        if not isinstance(data, dict):
            raise TypeError("alert payload must be a JSON object")
        if data.get("received_at") is None:
            data["received_at"] = datetime.now(UTC)
        alert = IncomingAlert.model_validate(data)
    except (TypeError, ValidationError, ValueError) as exc:
        # Expected client error: log the full detail, return only the exception
        # type (payload field names and model internals stay out of the
        # response), and skip Sentry capture for routine 400s.
        logger.warning("Alert payload rejected (%s): %s", type(exc).__name__, exc)
        return JSONResponse(
            {"error": f"invalid alert payload: {type(exc).__name__}"},
            status_code=400,
        )

    inbox = _alert_inbox()
    accepted = inbox.put(alert)
    payload: dict[str, Any] = {"queued": True, "queue_depth": inbox.qsize}
    if not accepted:
        payload["dropped"] = inbox.dropped
        payload["warning"] = "inbox full, oldest alert dropped"
    return JSONResponse(payload, status_code=202)


class InvestigateRequest(BaseModel):
    raw_alert: dict[str, Any]
    alert_name: str | None = None
    pipeline_name: str | None = None
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
    if (auth_error := _gateway_auth_error(request)) is not None:
        return auth_error

    investigation_metadata = resolve_investigation_context(
        raw_alert=req.raw_alert,
        alert_name=req.alert_name,
        pipeline_name=req.pipeline_name,
        severity=req.severity,
    )
    try:
        result = run_investigation_payload(
            raw_alert=req.raw_alert,
            investigation_metadata=investigation_metadata,
        )
        return InvestigateResponse(**result)
    except Exception as exc:
        # Full detail (which may include internal paths, stack context, or
        # upstream error bodies) goes to logs/Sentry only. The HTTP response
        # carries just the exception type so it stays actionable without
        # exposing internals to the caller (CodeQL: information exposure
        # through an exception).
        logger.exception("Investigation failed")
        capture_exception(exc, context="gateway.http.webapp.investigate")
        return JSONResponse(
            {"error": f"investigation failed: {type(exc).__name__}"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
