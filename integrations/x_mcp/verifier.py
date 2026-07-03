"""X MCP integration verifier."""

from __future__ import annotations

from integrations.verification import register_validation_verifier
from integrations.x_mcp import build_x_mcp_config, validate_x_mcp_config

verify_x_mcp = register_validation_verifier(
    "x_mcp",
    build_config=build_x_mcp_config,
    validate_config=validate_x_mcp_config,
)
