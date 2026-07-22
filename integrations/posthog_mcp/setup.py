"""What the hosted PostHog MCP server needs before it is considered configured.

Only the fields a user is ever actually prompted for. Transport is fixed to
Streamable HTTP in practice (the CLI never offers a stdio prompt — see
``surfaces/cli/wizard/configurators/posthog.py``), so ``mode``/``command``/
``args``/``read_only`` are left off this spec entirely: ``PostHogMCPConfig``'s
own field defaults already match what every setup path hard-coded, so omitting
them here is equivalent to writing them, without persisting values nobody
chose.
"""

from __future__ import annotations

from config.constants.posthog_mcp import (
    POSTHOG_MCP_AUTH_TOKEN_ENV,
    POSTHOG_MCP_PROJECT_ID_ENV,
    POSTHOG_MCP_URL_ENV,
)
from integrations.posthog_mcp import DEFAULT_POSTHOG_MCP_URL
from integrations.posthog_mcp.verifier import verify_posthog_mcp
from integrations.setup_flow import IntegrationSetupSpec, SetupField

URL_FIELD = "url"
AUTH_TOKEN_FIELD = "auth_token"
PROJECT_ID_FIELD = "project_id"

POSTHOG_MCP_SETUP = IntegrationSetupSpec(
    service="posthog_mcp",
    fields=(
        SetupField(
            name=URL_FIELD,
            label="PostHog MCP URL",
            env_var=POSTHOG_MCP_URL_ENV,
            default=DEFAULT_POSTHOG_MCP_URL,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="PostHog personal API key",
            prompt="PostHog personal API key (MCP Server preset)",
            env_var=POSTHOG_MCP_AUTH_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=PROJECT_ID_FIELD,
            label="PostHog project ID",
            prompt="PostHog project ID (optional)",
            env_var=POSTHOG_MCP_PROJECT_ID_ENV,
            required=False,
        ),
    ),
    verify=verify_posthog_mcp,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "POSTHOG_MCP_SETUP",
    "PROJECT_ID_FIELD",
    "URL_FIELD",
]
