"""What the local OpenClaw bridge needs before it is considered configured.

Transport is fixed to stdio — the local bridge. That value is a
:attr:`~integrations.setup_flow.SetupField.constant` rather than a prompt,
because ``OpenClawConfig`` defaults to ``streamable-http`` and omitting the
field would silently flip mode. ``url`` and ``auth_token`` stay empty constants
for the same reason the old CLI cleared them.
"""

from __future__ import annotations

from config.constants.openclaw import (
    OPENCLAW_MCP_ARGS_ENV,
    OPENCLAW_MCP_AUTH_TOKEN_ENV,
    OPENCLAW_MCP_COMMAND_ENV,
    OPENCLAW_MCP_MODE_ENV,
    OPENCLAW_MCP_URL_ENV,
)
from integrations.openclaw.verifier import verify_openclaw
from integrations.setup_flow import IntegrationSetupSpec, SetupField

MODE_FIELD = "mode"
COMMAND_FIELD = "command"
ARGS_FIELD = "args"
URL_FIELD = "url"
AUTH_TOKEN_FIELD = "auth_token"

OPENCLAW_SETUP = IntegrationSetupSpec(
    service="openclaw",
    fields=(
        SetupField(
            name=MODE_FIELD,
            label="OpenClaw MCP mode",
            env_var=OPENCLAW_MCP_MODE_ENV,
            constant="stdio",
        ),
        SetupField(
            name=COMMAND_FIELD,
            label="OpenClaw bridge command",
            env_var=OPENCLAW_MCP_COMMAND_ENV,
            default="openclaw",
        ),
        SetupField(
            name=ARGS_FIELD,
            label="OpenClaw bridge args",
            env_var=OPENCLAW_MCP_ARGS_ENV,
            default="mcp serve",
            required=False,
        ),
        SetupField(
            name=URL_FIELD,
            label="OpenClaw bridge URL",
            env_var=OPENCLAW_MCP_URL_ENV,
            constant="",
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="OpenClaw auth token",
            env_var=OPENCLAW_MCP_AUTH_TOKEN_ENV,
            secret=True,
            constant="",
        ),
    ),
    verify=verify_openclaw,
)

__all__ = [
    "ARGS_FIELD",
    "AUTH_TOKEN_FIELD",
    "COMMAND_FIELD",
    "MODE_FIELD",
    "OPENCLAW_SETUP",
    "URL_FIELD",
]
