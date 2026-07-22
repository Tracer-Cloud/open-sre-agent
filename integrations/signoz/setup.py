"""What SigNoz needs before it is considered configured."""

from __future__ import annotations

from config.constants.signoz import SIGNOZ_API_KEY_ENV, SIGNOZ_URL_ENV
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.signoz.verifier import verify_signoz

URL_FIELD = "url"
API_KEY_FIELD = "api_key"

SIGNOZ_SETUP = IntegrationSetupSpec(
    service="signoz",
    fields=(
        SetupField(
            name=URL_FIELD,
            label="SigNoz URL",
            prompt="SigNoz URL (e.g. http://localhost:8080 for local Docker)",
            env_var=SIGNOZ_URL_ENV,
        ),
        SetupField(
            name=API_KEY_FIELD,
            label="SigNoz API key",
            prompt="SigNoz API key (service account key)",
            env_var=SIGNOZ_API_KEY_ENV,
            secret=True,
        ),
    ),
    verify=verify_signoz,
)

__all__ = [
    "API_KEY_FIELD",
    "SIGNOZ_SETUP",
    "URL_FIELD",
]
