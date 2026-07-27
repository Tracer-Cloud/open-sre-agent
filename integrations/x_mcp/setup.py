"""What X's local MCP server needs before it is considered configured.

Only the fields a user is ever actually prompted for — see
``integrations/posthog_mcp/setup.py`` for why ``mode``/``command``/``args``/
``bearer_token`` are left off entirely.
"""

from __future__ import annotations

from config.constants.x_mcp import X_MCP_AUTH_TOKEN_ENV, X_MCP_URL_ENV
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.x_mcp import DEFAULT_X_MCP_URL
from integrations.x_mcp.verifier import verify_x_mcp

URL_FIELD = "url"
AUTH_TOKEN_FIELD = "auth_token"

X_MCP_SETUP = IntegrationSetupSpec(
    service="x_mcp",
    fields=(
        SetupField(
            name=URL_FIELD,
            label="X MCP URL",
            env_var=X_MCP_URL_ENV,
            default=DEFAULT_X_MCP_URL,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="Auth token for a tunneled/proxied endpoint",
            prompt="Auth token for a tunneled/proxied endpoint (optional)",
            env_var=X_MCP_AUTH_TOKEN_ENV,
            secret=True,
            required=False,
        ),
    ),
    verify=verify_x_mcp,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "URL_FIELD",
    "X_MCP_SETUP",
]
