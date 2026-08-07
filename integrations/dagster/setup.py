"""What Dagster needs before it is considered configured."""

from __future__ import annotations

from config.constants.dagster import DAGSTER_API_TOKEN_ENV, DAGSTER_ENDPOINT_ENV
from integrations.dagster.verifier import verify_dagster
from integrations.setup_flow import IntegrationSetupSpec, SetupField

ENDPOINT_FIELD = "endpoint"
API_TOKEN_FIELD = "api_token"

DAGSTER_SETUP = IntegrationSetupSpec(
    service="dagster",
    fields=(
        SetupField(
            name=ENDPOINT_FIELD,
            label="Dagster GraphQL endpoint",
            prompt=(
                "Dagster GraphQL endpoint "
                "(e.g. http://localhost:3000 or https://<org>.dagster.plus/<deployment>)"
            ),
            env_var=DAGSTER_ENDPOINT_ENV,
            default="http://localhost:3000",
        ),
        SetupField(
            name=API_TOKEN_FIELD,
            label="Dagster Cloud API token",
            prompt="Dagster Cloud API token (leave empty for local OSS Dagster with no auth)",
            env_var=DAGSTER_API_TOKEN_ENV,
            secret=True,
            required=False,
        ),
    ),
    verify=verify_dagster,
)

__all__ = [
    "API_TOKEN_FIELD",
    "DAGSTER_SETUP",
    "ENDPOINT_FIELD",
]
