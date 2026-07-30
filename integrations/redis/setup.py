"""What Redis needs before it is considered configured.

``ssl`` used to be gathered via a CLI select menu; every other field was
always asked. Treating it as a defaulted text field keeps the same credentials
without a branching prompt feature.
"""

from __future__ import annotations

from config.constants.redis import (
    REDIS_DATABASE_ENV,
    REDIS_HOST_ENV,
    REDIS_PASSWORD_ENV,
    REDIS_PORT_ENV,
    REDIS_SSL_ENV,
    REDIS_USERNAME_ENV,
)
from integrations.redis.verifier import verify_redis
from integrations.setup_flow import IntegrationSetupSpec, SetupField

HOST_FIELD = "host"
PORT_FIELD = "port"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
DB_FIELD = "db"
SSL_FIELD = "ssl"

REDIS_SETUP = IntegrationSetupSpec(
    service="redis",
    fields=(
        SetupField(
            name=HOST_FIELD,
            label="Host",
            prompt="Host (e.g. localhost or redis.example.net)",
            env_var=REDIS_HOST_ENV,
        ),
        SetupField(
            name=PORT_FIELD,
            label="Port",
            env_var=REDIS_PORT_ENV,
            default="6379",
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            prompt="Username (leave blank unless using Redis ACLs)",
            env_var=REDIS_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            prompt="Password (leave blank if not set)",
            env_var=REDIS_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=DB_FIELD,
            label="Database number",
            env_var=REDIS_DATABASE_ENV,
            default="0",
        ),
        SetupField(
            name=SSL_FIELD,
            label="Use TLS",
            prompt="Use TLS (true or false)",
            env_var=REDIS_SSL_ENV,
            default="false",
        ),
    ),
    verify=verify_redis,
)

__all__ = [
    "DB_FIELD",
    "HOST_FIELD",
    "PASSWORD_FIELD",
    "PORT_FIELD",
    "REDIS_SETUP",
    "SSL_FIELD",
    "USERNAME_FIELD",
]
