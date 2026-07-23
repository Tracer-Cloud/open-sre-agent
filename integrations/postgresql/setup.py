"""What PostgreSQL needs before it is considered configured.

``ssl_mode`` used to be gathered via a CLI select menu; every other field was
always asked. Treating it as a defaulted text field keeps the same credentials
without a branching prompt feature.
"""

from __future__ import annotations

from config.constants.postgresql import (
    POSTGRESQL_DATABASE_ENV,
    POSTGRESQL_HOST_ENV,
    POSTGRESQL_PASSWORD_ENV,
    POSTGRESQL_PORT_ENV,
    POSTGRESQL_SSL_MODE_ENV,
    POSTGRESQL_USERNAME_ENV,
)
from integrations.postgresql import DEFAULT_POSTGRESQL_SSL_MODE, DEFAULT_POSTGRESQL_USER
from integrations.postgresql.verifier import verify_postgresql
from integrations.setup_flow import IntegrationSetupSpec, SetupField

HOST_FIELD = "host"
PORT_FIELD = "port"
DATABASE_FIELD = "database"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
SSL_MODE_FIELD = "ssl_mode"

POSTGRESQL_SETUP = IntegrationSetupSpec(
    service="postgresql",
    fields=(
        SetupField(
            name=HOST_FIELD,
            label="Host",
            prompt="Host (e.g. localhost or postgres.example.com)",
            env_var=POSTGRESQL_HOST_ENV,
        ),
        SetupField(
            name=DATABASE_FIELD,
            label="Database name",
            env_var=POSTGRESQL_DATABASE_ENV,
        ),
        SetupField(
            name=PORT_FIELD,
            label="Port",
            env_var=POSTGRESQL_PORT_ENV,
            default="5432",
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            env_var=POSTGRESQL_USERNAME_ENV,
            default=DEFAULT_POSTGRESQL_USER,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            env_var=POSTGRESQL_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=SSL_MODE_FIELD,
            label="SSL mode",
            prompt="SSL mode (prefer, require, or disable)",
            env_var=POSTGRESQL_SSL_MODE_ENV,
            default=DEFAULT_POSTGRESQL_SSL_MODE,
        ),
    ),
    verify=verify_postgresql,
)

__all__ = [
    "DATABASE_FIELD",
    "HOST_FIELD",
    "PASSWORD_FIELD",
    "PORT_FIELD",
    "POSTGRESQL_SETUP",
    "SSL_MODE_FIELD",
    "USERNAME_FIELD",
]
