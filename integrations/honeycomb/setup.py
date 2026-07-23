"""What Honeycomb needs before it is considered configured.

Only the key is required. ``dataset`` defaults to ``__all__`` — the environment-
wide scope — because narrowing it is a preference, not a prerequisite, and
``base_url`` moves only for EU tenants.
"""

from __future__ import annotations

from config.constants.honeycomb import (
    HONEYCOMB_API_KEY_ENV,
    HONEYCOMB_BASE_URL_ENV,
    HONEYCOMB_DATASET_ENV,
)
from integrations.config_models import DEFAULT_HONEYCOMB_BASE_URL, DEFAULT_HONEYCOMB_DATASET
from integrations.honeycomb.verifier import verify_honeycomb
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_KEY_FIELD = "api_key"
DATASET_FIELD = "dataset"
BASE_URL_FIELD = "base_url"

HONEYCOMB_SETUP = IntegrationSetupSpec(
    service="honeycomb",
    fields=(
        SetupField(
            name=API_KEY_FIELD,
            label="Honeycomb API key",
            prompt="Configuration API key",
            env_var=HONEYCOMB_API_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=DATASET_FIELD,
            label="Honeycomb dataset",
            prompt="Dataset slug or __all__",
            env_var=HONEYCOMB_DATASET_ENV,
            default=DEFAULT_HONEYCOMB_DATASET,
        ),
        SetupField(
            name=BASE_URL_FIELD,
            label="Honeycomb API URL",
            prompt="API URL",
            env_var=HONEYCOMB_BASE_URL_ENV,
            default=DEFAULT_HONEYCOMB_BASE_URL,
        ),
    ),
    verify=verify_honeycomb,
)

__all__ = [
    "API_KEY_FIELD",
    "BASE_URL_FIELD",
    "DATASET_FIELD",
    "HONEYCOMB_SETUP",
]
