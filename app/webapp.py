from __future__ import annotations

<<<<<<< HEAD
import os
from pydantic import BaseModel
from fastapi import FastAPI, Header, HTTPException
from app.integrations.telegram import send_message
=======
from fastapi import FastAPI, Response, status
from pydantic import BaseModel, ValidationError

from app.config import LLMSettings, get_environment
from app.utils.sentry_sdk import init_sentry
from app.version import get_version

init_sentry(entrypoint="webapp")


class HealthResponse(BaseModel):
    ok: bool
    version: str
    llm_configured: bool
    env: str

>>>>>>> upstream/main

app = FastAPI()


<<<<<<< HEAD
class Alert(BaseModel):
    service: str
    severity: str
    message: str


def verify_api_key(x_api_key: str | None):
    api_key = os.getenv("ALERT_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: ALERT_API_KEY not set",
        )

    if not x_api_key or x_api_key != api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/alert")
def send_alert(alert: Alert, x_api_key: str | None = Header(None)) -> dict:
    verify_api_key(x_api_key)

    text = f"""
🚨 OpenSRE ALERT 🚨

Service: {alert.service}
Severity: {alert.severity}
Message: {alert.message}
"""

    result = send_message(text)

    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=result.get("error", "Failed to send alert"),
        )

    return {"status": "alert sent"}


@app.get("/health")
def health() -> dict:
    """
    Basic health check.
    Can be extended later without breaking compatibility.
    """
    return {
        "status": "ok",
        "service": "opensre",
    }
=======
def _llm_configured() -> bool:
    try:
        LLMSettings.from_env()
    except ValidationError:
        return False
    return True


def get_health_response() -> HealthResponse:
    llm_configured = _llm_configured()

    return HealthResponse(
        ok=llm_configured,
        version=get_version(),
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
>>>>>>> upstream/main
