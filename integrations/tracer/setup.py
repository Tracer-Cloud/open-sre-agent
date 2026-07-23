"""What Tracer needs before it is considered configured."""

from __future__ import annotations

from config.constants.tracer import TRACER_BASE_URL_ENV, TRACER_JWT_TOKEN_ENV
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.tracer.verifier import verify_tracer

BASE_URL_FIELD = "base_url"
JWT_TOKEN_FIELD = "jwt_token"

TRACER_SETUP = IntegrationSetupSpec(
    service="tracer",
    fields=(
        SetupField(
            name=BASE_URL_FIELD,
            label="Tracer web app URL",
            env_var=TRACER_BASE_URL_ENV,
            default="http://localhost:3000",
        ),
        SetupField(
            name=JWT_TOKEN_FIELD,
            label="Tracer JWT token",
            prompt="JWT token",
            env_var=TRACER_JWT_TOKEN_ENV,
            secret=True,
        ),
    ),
    verify=verify_tracer,
)

__all__ = [
    "BASE_URL_FIELD",
    "JWT_TOKEN_FIELD",
    "TRACER_SETUP",
]
