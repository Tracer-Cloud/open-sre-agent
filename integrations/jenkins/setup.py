"""What Jenkins needs before it is considered configured."""

from __future__ import annotations

from config.constants.jenkins import (
    JENKINS_API_TOKEN_ENV,
    JENKINS_BASE_URL_ENV,
    JENKINS_USERNAME_ENV,
)
from integrations.jenkins.verifier import verify_jenkins
from integrations.setup_flow import IntegrationSetupSpec, SetupField

BASE_URL_FIELD = "base_url"
USERNAME_FIELD = "username"
API_TOKEN_FIELD = "api_token"

JENKINS_SETUP = IntegrationSetupSpec(
    service="jenkins",
    fields=(
        SetupField(
            name=BASE_URL_FIELD,
            label="Jenkins URL",
            prompt="Jenkins URL (e.g. http://localhost:8080)",
            env_var=JENKINS_BASE_URL_ENV,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Jenkins username",
            env_var=JENKINS_USERNAME_ENV,
        ),
        SetupField(
            name=API_TOKEN_FIELD,
            label="Jenkins API token",
            env_var=JENKINS_API_TOKEN_ENV,
            secret=True,
        ),
    ),
    verify=verify_jenkins,
)

__all__ = [
    "API_TOKEN_FIELD",
    "BASE_URL_FIELD",
    "JENKINS_SETUP",
    "USERNAME_FIELD",
]
