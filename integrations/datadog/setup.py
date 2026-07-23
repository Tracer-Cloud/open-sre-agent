"""What Datadog needs before it is considered configured.

Both keys are required and neither substitutes for the other: the API key
authenticates ingestion, the application key authorizes the read endpoints every
Datadog tool calls. A setup with only one verifies against nothing useful.

``site`` decides which regional API host is contacted, so a US-default account
in the EU fails every query with a confusing 403 rather than a routing error.
It is prompted with a default rather than inferred.
"""

from __future__ import annotations

from config.constants.datadog import (
    DATADOG_API_KEY_ENV,
    DATADOG_APP_KEY_ENV,
    DATADOG_SITE_ENV,
)
from integrations.config_models import DEFAULT_DATADOG_SITE
from integrations.datadog.verifier import verify_datadog
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_KEY_FIELD = "api_key"
APP_KEY_FIELD = "app_key"
SITE_FIELD = "site"

DATADOG_SETUP = IntegrationSetupSpec(
    service="datadog",
    fields=(
        SetupField(
            name=API_KEY_FIELD,
            label="Datadog API key",
            env_var=DATADOG_API_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=APP_KEY_FIELD,
            label="Datadog application key",
            env_var=DATADOG_APP_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=SITE_FIELD,
            label="Datadog site",
            prompt="Site (e.g. datadoghq.com, datadoghq.eu)",
            env_var=DATADOG_SITE_ENV,
            default=DEFAULT_DATADOG_SITE,
        ),
    ),
    verify=verify_datadog,
)

__all__ = [
    "API_KEY_FIELD",
    "APP_KEY_FIELD",
    "DATADOG_SETUP",
    "SITE_FIELD",
]
