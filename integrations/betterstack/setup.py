"""What Better Stack needs before it is considered configured.

``sources`` is an optional planner hint: a comma-separated list of base IDs
from the Better Stack dashboard. It is persisted as the same comma-string the
``.env`` reader already understands; ``betterstack_extract_params`` normalizes
either a string or a legacy list so setup and runtime agree.
"""

from __future__ import annotations

from config.constants.betterstack import (
    BETTERSTACK_PASSWORD_ENV,
    BETTERSTACK_QUERY_ENDPOINT_ENV,
    BETTERSTACK_SOURCES_ENV,
    BETTERSTACK_USERNAME_ENV,
)
from integrations.betterstack.verifier import verify_betterstack
from integrations.setup_flow import IntegrationSetupSpec, SetupField

QUERY_ENDPOINT_FIELD = "query_endpoint"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
SOURCES_FIELD = "sources"

BETTERSTACK_SETUP = IntegrationSetupSpec(
    service="betterstack",
    fields=(
        SetupField(
            name=QUERY_ENDPOINT_FIELD,
            label="Better Stack SQL query endpoint",
            prompt=(
                "Better Stack SQL query endpoint "
                "(e.g. https://eu-nbg-2-connect.betterstackdata.com)"
            ),
            env_var=BETTERSTACK_QUERY_ENDPOINT_ENV,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Better Stack username",
            prompt="Better Stack username (Integrations > Connect ClickHouse HTTP client)",
            env_var=BETTERSTACK_USERNAME_ENV,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Better Stack password",
            env_var=BETTERSTACK_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=SOURCES_FIELD,
            label="Better Stack sources",
            prompt=(
                "Better Stack sources, comma-separated base IDs from dashboard "
                "(optional hint for the planner)"
            ),
            env_var=BETTERSTACK_SOURCES_ENV,
            required=False,
        ),
    ),
    verify=verify_betterstack,
)

__all__ = [
    "BETTERSTACK_SETUP",
    "PASSWORD_FIELD",
    "QUERY_ENDPOINT_FIELD",
    "SOURCES_FIELD",
    "USERNAME_FIELD",
]
