"""What the hosted Sentry MCP server needs before it is considered configured.

Only the fields a user is ever actually prompted for — see
``integrations/posthog_mcp/setup.py`` for why ``mode``/``command``/``args``
are left off entirely.
"""

from __future__ import annotations

from config.constants.sentry_mcp import (
    SENTRY_MCP_AUTH_TOKEN_ENV,
    SENTRY_MCP_HOST_ENV,
    SENTRY_MCP_URL_ENV,
)
from integrations.sentry_mcp import DEFAULT_SENTRY_MCP_URL
from integrations.sentry_mcp.verifier import verify_sentry_mcp
from integrations.setup_flow import IntegrationSetupSpec, SetupField

URL_FIELD = "url"
AUTH_TOKEN_FIELD = "auth_token"
HOST_FIELD = "host"

SENTRY_MCP_SETUP = IntegrationSetupSpec(
    service="sentry_mcp",
    fields=(
        SetupField(
            name=URL_FIELD,
            label="Sentry MCP URL",
            env_var=SENTRY_MCP_URL_ENV,
            default=DEFAULT_SENTRY_MCP_URL,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="Sentry user auth token",
            env_var=SENTRY_MCP_AUTH_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=HOST_FIELD,
            label="Self-hosted Sentry host",
            prompt="Self-hosted Sentry host (optional)",
            env_var=SENTRY_MCP_HOST_ENV,
            required=False,
        ),
    ),
    verify=verify_sentry_mcp,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "HOST_FIELD",
    "SENTRY_MCP_SETUP",
    "URL_FIELD",
]
