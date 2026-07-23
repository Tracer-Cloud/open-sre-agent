"""What Alertmanager needs before it is considered configured.

Auth is a picker (none / bearer / basic), so a single choice keeps bearer and
basic mutually exclusive. The XOR is also enforced by ``AlertmanagerConfig``
(the model :func:`integrations.alertmanager.verifier.verify_alertmanager`
validates against rejects bearer + basic together), so setup and health checks
agree for any surface that skips the picker. The URL is always asked; the picker
only scopes the auth fields.
"""

from __future__ import annotations

from config.constants.alertmanager import (
    ALERTMANAGER_BEARER_TOKEN_ENV,
    ALERTMANAGER_PASSWORD_ENV,
    ALERTMANAGER_URL_ENV,
    ALERTMANAGER_USERNAME_ENV,
)
from integrations.alertmanager.verifier import verify_alertmanager
from integrations.setup_flow import IntegrationSetupSpec, SetupField, SetupMode

BASE_URL_FIELD = "base_url"
BEARER_TOKEN_FIELD = "bearer_token"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"


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
            prompt="Bearer token",
            env_var=ALERTMANAGER_BEARER_TOKEN_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            prompt="Username",
            env_var=ALERTMANAGER_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            prompt="Password",
            env_var=ALERTMANAGER_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
    ),
    mode_prompt="Authentication method:",
    modes=(
        SetupMode(value="none", label="None (unauthenticated / internal network)"),
        SetupMode(
            value="bearer",
            label="Bearer token (reverse proxy auth)",
            fields=(BEARER_TOKEN_FIELD,),
        ),
        SetupMode(
            value="basic",
            label="Basic auth (username + password)",
            fields=(USERNAME_FIELD, PASSWORD_FIELD),
        ),
    ),
    verify=verify_alertmanager,
)

__all__ = [
    "ALERTMANAGER_SETUP",
    "BASE_URL_FIELD",
    "BEARER_TOKEN_FIELD",
    "PASSWORD_FIELD",
    "USERNAME_FIELD",
]
