"""What Rootly needs before it is considered configured."""

from __future__ import annotations

from config.constants.rootly import (
    ROOTLY_API_TOKEN_ENV,
    ROOTLY_BASE_URL_ENV,
    ROOTLY_TIMEOUT_SECONDS_ENV,
)
from integrations.rootly.verifier import verify_rootly
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_TOKEN_FIELD = "api_token"
BASE_URL_FIELD = "base_url"
TIMEOUT_SECONDS_FIELD = "timeout_seconds"

ROOTLY_SETUP = IntegrationSetupSpec(
    service="rootly",
    fields=(
        SetupField(
            name=API_TOKEN_FIELD,
            label="Rootly API token",
            env_var=ROOTLY_API_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=BASE_URL_FIELD,
            label="Rootly API base URL",
            prompt="API base URL override (optional)",
            env_var=ROOTLY_BASE_URL_ENV,
            required=False,
        ),
        SetupField(
            name=TIMEOUT_SECONDS_FIELD,
            label="Rootly request timeout (seconds)",
            prompt="Request timeout in seconds (optional)",
            env_var=ROOTLY_TIMEOUT_SECONDS_ENV,
            required=False,
        ),
    ),
    verify=verify_rootly,
)

__all__ = [
    "API_TOKEN_FIELD",
    "BASE_URL_FIELD",
    "ROOTLY_SETUP",
    "TIMEOUT_SECONDS_FIELD",
]
