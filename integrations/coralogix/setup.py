"""What Coralogix needs before it is considered configured.

Only the DataPrime key is required. ``base_url`` is regional and defaults to the
US host. The application and subsystem names are optional query filters — left
blank, queries span the whole account.
"""

from __future__ import annotations

from config.constants.coralogix import (
    CORALOGIX_API_KEY_ENV,
    CORALOGIX_APPLICATION_NAME_ENV,
    CORALOGIX_BASE_URL_ENV,
    CORALOGIX_SUBSYSTEM_NAME_ENV,
)
from integrations.config_models import DEFAULT_CORALOGIX_BASE_URL
from integrations.coralogix.verifier import verify_coralogix
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_KEY_FIELD = "api_key"
BASE_URL_FIELD = "base_url"
APPLICATION_NAME_FIELD = "application_name"
SUBSYSTEM_NAME_FIELD = "subsystem_name"

CORALOGIX_SETUP = IntegrationSetupSpec(
    service="coralogix",
    fields=(
        SetupField(
            name=API_KEY_FIELD,
            label="Coralogix API key",
            prompt="DataPrime API key",
            env_var=CORALOGIX_API_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=BASE_URL_FIELD,
            label="Coralogix API URL",
            prompt="API URL",
            env_var=CORALOGIX_BASE_URL_ENV,
            default=DEFAULT_CORALOGIX_BASE_URL,
        ),
        SetupField(
            name=APPLICATION_NAME_FIELD,
            label="Coralogix application name",
            prompt="Application name (optional)",
            env_var=CORALOGIX_APPLICATION_NAME_ENV,
            required=False,
        ),
        SetupField(
            name=SUBSYSTEM_NAME_FIELD,
            label="Coralogix subsystem name",
            prompt="Subsystem name (optional)",
            env_var=CORALOGIX_SUBSYSTEM_NAME_ENV,
            required=False,
        ),
    ),
    verify=verify_coralogix,
)

__all__ = [
    "API_KEY_FIELD",
    "APPLICATION_NAME_FIELD",
    "BASE_URL_FIELD",
    "CORALOGIX_SETUP",
    "SUBSYSTEM_NAME_FIELD",
]
