"""Sentry MCP integration verifier."""

from __future__ import annotations

from integrations.sentry_mcp import build_sentry_mcp_config, validate_sentry_mcp_config
from integrations.verification import register_validation_verifier

verify_sentry_mcp = register_validation_verifier(
    "sentry_mcp",
    build_config=build_sentry_mcp_config,
    validate_config=validate_sentry_mcp_config,
)
