"""What MariaDB needs before it is considered configured.

``ssl`` used to be gathered via a CLI select menu; every other field was
always asked. Treating it as a defaulted text field keeps the same credentials
without a branching prompt feature.
"""

from __future__ import annotations

from config.constants.mariadb import (
    MARIADB_DATABASE_ENV,
    MARIADB_HOST_ENV,
    MARIADB_PASSWORD_ENV,
    MARIADB_PORT_ENV,
    MARIADB_SSL_ENV,
    MARIADB_USERNAME_ENV,
)
from integrations.mariadb.verifier import verify_mariadb
from integrations.setup_flow import IntegrationSetupSpec, SetupField

HOST_FIELD = "host"
PORT_FIELD = "port"
DATABASE_FIELD = "database"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
SSL_FIELD = "ssl"

MARIADB_SETUP = IntegrationSetupSpec(
    service="mariadb",
    fields=(
        SetupField(
            name=HOST_FIELD,
            label="Host",
            prompt="Host (e.g. db.example.com)",
            env_var=MARIADB_HOST_ENV,
        ),
        SetupField(
            name=DATABASE_FIELD,
            label="Database name",
            env_var=MARIADB_DATABASE_ENV,
        ),
        SetupField(
            name=PORT_FIELD,
            label="Port",
            env_var=MARIADB_PORT_ENV,
            default="3306",
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            env_var=MARIADB_USERNAME_ENV,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            env_var=MARIADB_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=SSL_FIELD,
            label="SSL enabled",
            prompt="SSL enabled (true or false)",
            env_var=MARIADB_SSL_ENV,
            default="true",
        ),
    ),
    verify=verify_mariadb,
)

__all__ = [
    "DATABASE_FIELD",
    "HOST_FIELD",
    "MARIADB_SETUP",
    "PASSWORD_FIELD",
    "PORT_FIELD",
    "SSL_FIELD",
    "USERNAME_FIELD",
]
