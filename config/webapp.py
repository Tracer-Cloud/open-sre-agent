from __future__ import annotations

from fastapi import FastAPI, Response, status
from pydantic import BaseModel, ValidationError

from config.config import LLMSettings, get_environment
from config.platform_bootstrap import ensure_project_platform_package
from config.version import get_opensre_version

ensure_project_platform_package()

from platform.observability.sentry_sdk import init_sentry  # noqa: E402

init_sentry(entrypoint="webapp")


class HealthResponse(BaseModel):
    ok: bool
    version: str
    llm_configured: bool
    env: str


app = FastAPI()


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
