"""What MySQL needs before it is considered configured.

Same shape as PostgreSQL: ``ssl_mode`` is a defaulted text field rather than a
branching select, because every field was always collected.
"""

from __future__ import annotations

from config.constants.mysql import (
    MYSQL_DATABASE_ENV,
    MYSQL_HOST_ENV,
    MYSQL_PASSWORD_ENV,
    MYSQL_PORT_ENV,
    MYSQL_SSL_MODE_ENV,
    MYSQL_USERNAME_ENV,
)
from integrations.mysql import DEFAULT_MYSQL_SSL_MODE, DEFAULT_MYSQL_USER
from integrations.mysql.verifier import verify_mysql
from integrations.setup_flow import IntegrationSetupSpec, SetupField

HOST_FIELD = "host"
PORT_FIELD = "port"
DATABASE_FIELD = "database"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
SSL_MODE_FIELD = "ssl_mode"

MYSQL_SETUP = IntegrationSetupSpec(
    service="mysql",
    fields=(
        SetupField(
            name=HOST_FIELD,
            label="Host",
            prompt="Host (e.g. localhost or mysql.example.com)",
            env_var=MYSQL_HOST_ENV,
        ),
        SetupField(
            name=DATABASE_FIELD,
            label="Database name",
            env_var=MYSQL_DATABASE_ENV,
        ),
        SetupField(
            name=PORT_FIELD,
            label="Port",
            env_var=MYSQL_PORT_ENV,
            default="3306",
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            env_var=MYSQL_USERNAME_ENV,
            default=DEFAULT_MYSQL_USER,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            env_var=MYSQL_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=SSL_MODE_FIELD,
            label="SSL mode",
            prompt="SSL mode (preferred, required, or disabled)",
            env_var=MYSQL_SSL_MODE_ENV,
            default=DEFAULT_MYSQL_SSL_MODE,
        ),
    ),
    verify=verify_mysql,
)

__all__ = [
    "DATABASE_FIELD",
    "HOST_FIELD",
    "MYSQL_SETUP",
    "PASSWORD_FIELD",
    "PORT_FIELD",
    "SSL_MODE_FIELD",
    "USERNAME_FIELD",
]
