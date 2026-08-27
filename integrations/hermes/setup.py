"""Setup contract for the local Hermes log integration."""

from __future__ import annotations

from config.constants.hermes import HERMES_LOG_PATH_ENV
from integrations.hermes.config import default_hermes_log_path
from integrations.hermes.verifier import verify_hermes
from integrations.setup_flow import IntegrationSetupSpec, SetupField

LOG_PATH_FIELD = "log_path"

HERMES_SETUP = IntegrationSetupSpec(
    service="hermes",
    fields=(
        SetupField(
            name=LOG_PATH_FIELD,
            label="Hermes log file",
            prompt="Path to the Hermes errors.log file",
            env_var=HERMES_LOG_PATH_ENV,
            default=str(default_hermes_log_path()),
        ),
    ),
    verify=verify_hermes,
)

__all__ = ["HERMES_SETUP", "LOG_PATH_FIELD"]
