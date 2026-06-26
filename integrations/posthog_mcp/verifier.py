"""PostHog MCP integration verifier."""

from __future__ import annotations

from integrations.posthog_mcp import build_posthog_mcp_config, validate_posthog_mcp_config
from integrations.verification import register_validation_verifier

verify_posthog_mcp = register_validation_verifier(
    "posthog_mcp",
    build_config=build_posthog_mcp_config,
    validate_config=validate_posthog_mcp_config,
)
