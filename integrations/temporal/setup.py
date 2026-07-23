"""What Temporal needs before it is considered configured."""

from __future__ import annotations

from config.constants.temporal import (
    TEMPORAL_API_KEY_ENV,
    TEMPORAL_BASE_URL_ENV,
    TEMPORAL_NAMESPACE_ENV,
)
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.temporal.verifier import verify_temporal

BASE_URL_FIELD = "base_url"
NAMESPACE_FIELD = "namespace"
API_KEY_FIELD = "api_key"

TEMPORAL_SETUP = IntegrationSetupSpec(
    service="temporal",
    fields=(
        SetupField(
            name=BASE_URL_FIELD,
            label="Temporal HTTP API base URL",
            prompt="Temporal HTTP API base URL (e.g. http://localhost:7243)",
            env_var=TEMPORAL_BASE_URL_ENV,
        ),
        SetupField(
            name=NAMESPACE_FIELD,
            label="Temporal namespace",
            env_var=TEMPORAL_NAMESPACE_ENV,
            default="default",
        ),
        SetupField(
            name=API_KEY_FIELD,
            label="Temporal API key",
            prompt="Temporal API key (leave empty for unauthenticated self-hosted clusters)",
            env_var=TEMPORAL_API_KEY_ENV,
            secret=True,
            required=False,
        ),
    ),
    verify=verify_temporal,
)

__all__ = [
    "API_KEY_FIELD",
    "BASE_URL_FIELD",
    "NAMESPACE_FIELD",
    "TEMPORAL_SETUP",
]
