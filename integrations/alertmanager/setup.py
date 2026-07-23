"""What Alertmanager needs before it is considered configured.

Auth used to be a branching select (none / bearer / basic). Every auth field is
independently optional — same shape as Tempo — but bearer and basic must not be
combined (the catalog model rejects that). ``validate`` enforces the XOR early.
"""

from __future__ import annotations

from config.constants.alertmanager import (
    ALERTMANAGER_BEARER_TOKEN_ENV,
    ALERTMANAGER_PASSWORD_ENV,
    ALERTMANAGER_URL_ENV,
    ALERTMANAGER_USERNAME_ENV,
)
from integrations.alertmanager.verifier import verify_alertmanager
from integrations.setup_flow import IntegrationSetupSpec, SetupField

BASE_URL_FIELD = "base_url"
BEARER_TOKEN_FIELD = "bearer_token"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"


def _reject_dual_auth(credentials: dict[str, str | None]) -> str:
    """Match AlertmanagerIntegrationConfig: bearer XOR basic, never both."""
    if credentials.get(BEARER_TOKEN_FIELD) and credentials.get(USERNAME_FIELD):
        return "Provide a bearer token or basic auth, not both."
    return ""


ALERTMANAGER_SETUP = IntegrationSetupSpec(
    service="alertmanager",
    fields=(
        SetupField(
            name=BASE_URL_FIELD,
            label="Alertmanager URL",
            prompt="Alertmanager URL (e.g. http://alertmanager:9093)",
            env_var=ALERTMANAGER_URL_ENV,
        ),
        SetupField(
            name=BEARER_TOKEN_FIELD,
            label="Bearer token",
            prompt="Bearer token (optional, leave blank if using basic auth or none)",
            env_var=ALERTMANAGER_BEARER_TOKEN_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            prompt="Username (optional, for basic auth)",
            env_var=ALERTMANAGER_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            prompt="Password (optional, for basic auth)",
            env_var=ALERTMANAGER_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
    ),
    validate=_reject_dual_auth,
    verify=verify_alertmanager,
)

__all__ = [
    "ALERTMANAGER_SETUP",
    "BASE_URL_FIELD",
    "BEARER_TOKEN_FIELD",
    "PASSWORD_FIELD",
    "USERNAME_FIELD",
]
