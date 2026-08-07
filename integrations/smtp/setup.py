"""What SMTP needs before it is considered configured."""

from __future__ import annotations

from config.constants.smtp import (
    SMTP_DEFAULT_TO_ENV,
    SMTP_FROM_ADDRESS_ENV,
    SMTP_HOST_ENV,
    SMTP_PASSWORD_ENV,
    SMTP_PORT_ENV,
    SMTP_SECURITY_ENV,
    SMTP_USERNAME_ENV,
)
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.smtp.verifier import verify_smtp

HOST_FIELD = "host"
PORT_FIELD = "port"
SECURITY_FIELD = "security"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
FROM_ADDRESS_FIELD = "from_address"
DEFAULT_TO_FIELD = "default_to"

SMTP_SETUP = IntegrationSetupSpec(
    service="smtp",
    fields=(
        SetupField(
            name=HOST_FIELD,
            label="SMTP host",
            prompt="SMTP host (e.g. smtp.gmail.com)",
            env_var=SMTP_HOST_ENV,
        ),
        SetupField(
            name=FROM_ADDRESS_FIELD,
            label="From email address",
            env_var=SMTP_FROM_ADDRESS_ENV,
        ),
        SetupField(
            name=PORT_FIELD,
            label="SMTP port",
            env_var=SMTP_PORT_ENV,
            default="587",
        ),
        SetupField(
            name=SECURITY_FIELD,
            label="Security mode",
            prompt="Security mode (starttls/ssl/none)",
            env_var=SMTP_SECURITY_ENV,
            default="starttls",
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            prompt="Username (optional)",
            env_var=SMTP_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            prompt="Password (optional; leave blank when username is blank)",
            env_var=SMTP_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=DEFAULT_TO_FIELD,
            label="Default recipient email",
            prompt="Default recipient email (optional)",
            env_var=SMTP_DEFAULT_TO_ENV,
            required=False,
        ),
    ),
    verify=verify_smtp,
)

__all__ = [
    "DEFAULT_TO_FIELD",
    "FROM_ADDRESS_FIELD",
    "HOST_FIELD",
    "PASSWORD_FIELD",
    "PORT_FIELD",
    "SECURITY_FIELD",
    "SMTP_SETUP",
    "USERNAME_FIELD",
]
