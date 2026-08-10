"""The gateway's single FastAPI app: health probes, alert intake, investigations.

Every HTTP endpoint OpenSRE serves lives here, on one port — ``/`` ``/health``
``/ok`` (health probes), ``/healthz`` (liveness), ``POST /alerts`` (external
alert pushes into the process-wide :class:`AlertInbox`), and ``POST /investigate``
(run an investigation synchronously and return the RCA report). Hosted by the
gateway daemon and the interactive shell via :mod:`gateway.web.web_server`, or
standalone via ``uvicorn gateway.web.webapp:app``.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from config.config import LLMSettings, get_environment
from config.constants.agent_identity import agent_name
from config.platform_bootstrap import ensure_project_platform_package
from config.version import get_opensre_version
from core.domain.alerts.inbox import (
    AlertInbox,
    IncomingAlert,
    get_current_inbox,
    set_current_inbox,
)

ensure_project_platform_package()

from bootstrap.process import WEB_PROFILE, configure_process  # noqa: E402
from core.agent_harness import AgentSession  # noqa: E402
from gateway.core.config.logging_config import configure_logging  # noqa: E402
from gateway.core.runtime.readiness import is_gateway_ready  # noqa: E402
from gateway.web.access_log import install_probe_access_log_filter  # noqa: E402
from gateway.web.investigations import router as investigations_router  # noqa: E402
from integrations.gcp.gke import start_gke_autoregistration  # noqa: E402
from platform.observability.errors.sentry import capture_exception  # noqa: E402
from tools.investigation.capability import resolve_investigation_context  # noqa: E402

# Standalone uvicorn and in-process gateway both need adapters for /investigate.
# Shared boot order lives in bootstrap.process (env → sentry → adapters).
configure_process(WEB_PROFILE)

# Kubernetes probes every few seconds would otherwise bury the access log in
# identical 200s. Failing probes still log.
install_probe_access_log_filter()

# uvicorn attaches handlers to the `uvicorn*` loggers only and leaves the root
# logger bare, so every application log line below WARNING is swallowed by
# `logging.lastResort` — the web pod's own output was four uvicorn banner lines
# and nothing else. The gateway process has always called this; the web process
# never did, which is why a successful GKE registration here left no trace while
# the identical code logged normally in the gateway. Idempotent: it no-ops when
# the root logger already has a handler, so the in-process case
# (`serve_webapp_in_thread`, where the gateway configured logging first) keeps
# the gateway's formatting rather than getting a second one.
configure_logging()

logger = logging.getLogger(__name__)

# Opt-in and backgrounded (off unless GCP_AUTO_REGISTER_GKE is set). The web
# process runs investigations too, so it needs the same registered clusters the
# gateway has — and it gets its own container filesystem, so a cluster added by
# hand in the gateway pod is invisible here. Backgrounded because discovery is an
# unbounded remote call and the readiness probe starts at 10s.
start_gke_autoregistration(logger)

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
    response.status_code = HTTPStatus.OK if health_response.ok else HTTPStatus.SERVICE_UNAVAILABLE
    return health_response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Report mandatory startup readiness separately from process liveness."""
    if is_gateway_ready():
        return JSONResponse({"status": "ready"}, status_code=HTTPStatus.OK)
    return JSONResponse({"status": "not_ready"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE)


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
        return JSONResponse({"error": "unauthorized"}, status_code=HTTPStatus.UNAUTHORIZED)
    client_host = request.client.host if request.client else ""
    if client_host in _LOOPBACK_HOSTS:
        return None
    return JSONResponse(
        {"error": "set OPENSRE_ALERT_LISTENER_TOKEN to accept non-loopback callers"},
        status_code=HTTPStatus.FORBIDDEN,
    )


@app.post("/alerts")
async def receive_alert(request: Request) -> JSONResponse:
    if (auth_error := _gateway_auth_error(request)) is not None:
        return auth_error

    try:
        declared_length = int(request.headers.get("content-length", 0))
    except ValueError:
        return JSONResponse({"error": "invalid Content-Length"}, status_code=HTTPStatus.BAD_REQUEST)
    if declared_length < 0:
        return JSONResponse({"error": "invalid Content-Length"}, status_code=HTTPStatus.BAD_REQUEST)
    if declared_length > MAX_ALERT_BODY_BYTES:
        return JSONResponse(
            {"error": "payload too large"}, status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        )

    body = await request.body()
    if len(body) > MAX_ALERT_BODY_BYTES:
        return JSONResponse(
            {"error": "payload too large"}, status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        )

    try:
        data = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "invalid json"}, status_code=HTTPStatus.BAD_REQUEST)

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
            status_code=HTTPStatus.BAD_REQUEST,
        )

    inbox = _alert_inbox()
    accepted = inbox.put(alert)
    payload: dict[str, Any] = {"queued": True, "queue_depth": inbox.qsize}
    if not accepted:
        payload["dropped"] = inbox.dropped
        payload["warning"] = "inbox full, oldest alert dropped"
    return JSONResponse(payload, status_code=HTTPStatus.ACCEPTED)


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
    if (auth_error := _gateway_auth_error(request)) is not None:
        return auth_error

    from gateway.core.runtime.concurrency import process_turn_gate

    gate = process_turn_gate()
    # Same process gate as chat / scheduler — busy-drop like GatewayTurnHandler.
    if not gate.try_acquire():
        return JSONResponse(
            {"error": f"{agent_name()} is at capacity. Please try again shortly."},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )

    investigation_metadata = resolve_investigation_context(
        raw_alert=req.raw_alert,
        alert_name=req.alert_name,
        severity=req.severity,
    )
    try:
        result = AgentSession().investigate(
            req.raw_alert,
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
    finally:
        gate.release()
