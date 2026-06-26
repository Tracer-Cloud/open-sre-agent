"""Streamlined GitHub device-flow login that configures the hosted GitHub MCP integration.

Used by the first-launch gate to authenticate the user, persist the hosted
GitHub MCP integration, and surface the authenticated GitHub username. Reuses
the same hosted defaults as ``opensre integrations setup github`` (no transport
or advanced prompts).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from integrations.github_mcp import (
    DEFAULT_GITHUB_MCP_MODE,
    DEFAULT_GITHUB_MCP_TOOLSETS,
    DEFAULT_GITHUB_MCP_URL,
    build_github_mcp_config,
    validate_github_mcp_config,
)
from integrations.github_mcp_oauth import (
    GitHubDeviceCode,
    authorize_github_via_device_flow,
)
from integrations.store import upsert_integration


@dataclass(frozen=True)
class GitHubLoginResult:
    """Outcome of a device-flow login + hosted GitHub MCP configuration attempt."""

    ok: bool
    username: str = ""
    detail: str = ""


def authenticate_and_configure_github(
    *,
    on_prompt: Callable[[GitHubDeviceCode], None] | None = None,
    open_browser: bool = True,
) -> GitHubLoginResult:
    """Run device-flow login, validate, and persist the hosted GitHub MCP integration.

    The device flow may raise ``GitHubDeviceFlowError`` (or transport errors); those
    propagate to the caller so it can present a message and decide whether to retry.
    The integration is persisted only when validation succeeds.
    """
    token = authorize_github_via_device_flow(on_prompt=on_prompt, open_browser=open_browser)
    credentials: dict[str, object] = {
        "mode": DEFAULT_GITHUB_MCP_MODE,
        "url": DEFAULT_GITHUB_MCP_URL,
        "auth_token": token.access_token,
        "toolsets": list(DEFAULT_GITHUB_MCP_TOOLSETS),
    }
    result = validate_github_mcp_config(build_github_mcp_config(credentials))
    if not result.ok:
        return GitHubLoginResult(
            ok=False,
            username=result.authenticated_user,
            detail=result.detail,
        )
    if result.authenticated_user:
        # Persist the resolved GitHub login as a non-secret credential field so
        # surfaces like the welcome banner can greet the user by their GitHub
        # handle instead of the local system username.
        credentials["username"] = result.authenticated_user
    upsert_integration("github", {"credentials": credentials})
    return GitHubLoginResult(ok=True, username=result.authenticated_user, detail=result.detail)
