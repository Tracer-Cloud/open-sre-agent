"""What MongoDB needs before it is considered configured.

``tls`` used to be gathered via a CLI select menu; every other field was
always asked. Treating it as a defaulted text field keeps the same credentials
without a branching prompt feature.
"""

from __future__ import annotations

from config.constants.mongodb import (
    MONGODB_AUTH_SOURCE_ENV,
    MONGODB_CONNECTION_STRING_ENV,
    MONGODB_DATABASE_ENV,
    MONGODB_TLS_ENV,
)
from integrations.mongodb import DEFAULT_MONGODB_AUTH_SOURCE
from integrations.mongodb.verifier import verify_mongodb
from integrations.setup_flow import IntegrationSetupSpec, SetupField

CONNECTION_STRING_FIELD = "connection_string"
DATABASE_FIELD = "database"
AUTH_SOURCE_FIELD = "auth_source"
TLS_FIELD = "tls"

MONGODB_SETUP = IntegrationSetupSpec(
    service="mongodb",
    fields=(
        SetupField(
            name=CONNECTION_STRING_FIELD,
            label="Connection string",
            prompt="Connection string (e.g. mongodb+srv://user:pass@cluster.example.net)",
            env_var=MONGODB_CONNECTION_STRING_ENV,
            secret=True,
        ),
        SetupField(
            name=DATABASE_FIELD,
            label="Database name",
            env_var=MONGODB_DATABASE_ENV,
            required=False,
        ),
        SetupField(
            name=AUTH_SOURCE_FIELD,
            label="Auth source",
            env_var=MONGODB_AUTH_SOURCE_ENV,
            default=DEFAULT_MONGODB_AUTH_SOURCE,
        ),
        SetupField(
            name=TLS_FIELD,
            label="TLS enabled",
            prompt="TLS enabled (true or false)",
            env_var=MONGODB_TLS_ENV,
            default="true",
        ),
    ),
    verify=verify_mongodb,
)

__all__ = [
    "AUTH_SOURCE_FIELD",
    "CONNECTION_STRING_FIELD",
    "DATABASE_FIELD",
    "MONGODB_SETUP",
    "TLS_FIELD",
]
