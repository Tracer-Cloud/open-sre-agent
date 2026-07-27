"""What incident.io needs before it is considered configured."""

from __future__ import annotations

from config.constants.incident_io import INCIDENT_IO_API_KEY_ENV, INCIDENT_IO_BASE_URL_ENV
from integrations.incident_io.verifier import verify_incident_io
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_KEY_FIELD = "api_key"
BASE_URL_FIELD = "base_url"

INCIDENT_IO_SETUP = IntegrationSetupSpec(
    service="incident_io",
    fields=(
        SetupField(
            name=API_KEY_FIELD,
            label="incident.io API key",
            env_var=INCIDENT_IO_API_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=BASE_URL_FIELD,
            label="incident.io API base URL",
            prompt="API base URL override (optional)",
            env_var=INCIDENT_IO_BASE_URL_ENV,
            required=False,
        ),
    ),
    verify=verify_incident_io,
)

__all__ = [
    "API_KEY_FIELD",
    "BASE_URL_FIELD",
    "INCIDENT_IO_SETUP",
]
