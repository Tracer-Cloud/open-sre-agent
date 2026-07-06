"""GitHub MCP integration verifier.

Reports credential-less records as ``missing`` rather than ``failed`` so
a stale store entry with no auth token surfaces as not-configured instead
of a confusing 401.
"""

from __future__ import annotations

from typing import Any

from integrations.github.mcp import build_github_mcp_config, validate_github_mcp_config
from integrations.verification import register_verifier, result


@register_verifier("github")
def verify_github(source: str, config: dict[str, Any]) -> dict[str, str]:
    normalized_config = build_github_mcp_config(config)
    validation_result = validate_github_mcp_config(normalized_config)
    if not validation_result.ok and validation_result.failure_category == "not_configured":
        return result("github", source, "missing", validation_result.detail)
    return result(
        "github",
        source,
        "passed" if validation_result.ok else "failed",
        validation_result.detail,
    )
