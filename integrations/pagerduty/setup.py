"""What PagerDuty needs before it is considered configured."""

from __future__ import annotations

from config.constants.pagerduty import PAGERDUTY_API_KEY_ENV, PAGERDUTY_BASE_URL_ENV
from integrations.pagerduty.verifier import verify_pagerduty
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_KEY_FIELD = "api_key"
BASE_URL_FIELD = "base_url"

PAGERDUTY_SETUP = IntegrationSetupSpec(
    service="pagerduty",
    fields=(
        SetupField(
            name=API_KEY_FIELD,
            label="PagerDuty API key",
            env_var=PAGERDUTY_API_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=BASE_URL_FIELD,
            label="PagerDuty API base URL",
            prompt="PagerDuty API base URL (press Enter to use default)",
            env_var=PAGERDUTY_BASE_URL_ENV,
            default="https://api.pagerduty.com",
        ),
    ),
    verify=verify_pagerduty,
)

__all__ = [
    "API_KEY_FIELD",
    "BASE_URL_FIELD",
    "PAGERDUTY_SETUP",
]
