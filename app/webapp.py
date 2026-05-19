from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Response, status
from pydantic import BaseModel, ValidationError

from app.config import LLMSettings, get_environment
from app.entrypoints.alerts import router as alerts_router
from app.entrypoints.investigations import router as investigations_router
from app.utils.sentry_sdk import init_sentry
from app.version import get_version

init_sentry(entrypoint="webapp")

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    ok: bool
    version: str
    llm_configured: bool
    env: str
    db: bool | None = None
    redis: bool | None = None


app = FastAPI()
app.include_router(investigations_router)
app.include_router(alerts_router)


def _llm_configured() -> bool:
    try:
        LLMSettings.from_env()
    except ValidationError:
        return False
    return True


def _db_ok() -> bool:
    db_uri = os.environ.get("DATABASE_URI")
    if not db_uri:
        return True  # not required in dev; absence is not a failure
    try:
        import psycopg2  # type: ignore[import-untyped]

        conn = psycopg2.connect(db_uri, connect_timeout=3)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        return True
    except Exception:
        logger.exception("DB health check failed")
        return False


def _redis_ok() -> bool:
    redis_uri = os.environ.get("REDIS_URI")
    if not redis_uri:
        return True  # not required in dev; absence is not a failure
    try:
        import redis as redis_lib

        client = redis_lib.from_url(redis_uri, socket_connect_timeout=3)
        client.ping()
        return True
    except Exception:
        logger.exception("Redis health check failed")
        return False


def get_health_response() -> HealthResponse:
    llm_configured = _llm_configured()
    db = _db_ok()
    redis = _redis_ok()
    ok = db and redis
    return HealthResponse(
        ok=ok,
        version=get_version(),
        llm_configured=llm_configured,
        env=get_environment().value,
        db=db,
        redis=redis,
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
