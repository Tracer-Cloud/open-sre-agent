"""Helper functions for GitHub tools."""

from __future__ import annotations

from typing import Any

from app.integrations.github_mcp import (
    GitHubMCPConfig,
    build_github_mcp_config,
    github_mcp_config_from_env,
)


def github_source_available(sources: dict[str, dict]) -> bool:
    """Check if source is available."""
    return bool(sources.get("github", {}).get("connection_verified"))


def github_creds(gh: dict) -> dict:
    """Get GitHub credentials."""
    return {
        "github_url": gh.get("github_url"),
        "github_mode": gh.get("github_mode", "streamable-http"),
        "github_token": gh.get("github_token"),
        "github_command": gh.get("github_command", ""),
        "github_args": gh.get("github_args", []),
    }


def resolve_github_mcp_config(
    github_url: str | None,
    github_mode: str | None,
    github_token: str | None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
) -> GitHubMCPConfig | None:
    """Resolve GitHub MCP config."""
    env_config = github_mcp_config_from_env()
    if any([github_url, github_mode, github_token, github_command, github_args]):
        return build_github_mcp_config(
            {
                "url": github_url or (env_config.url if env_config else ""),
                "mode": github_mode or (env_config.mode if env_config else ""),
                "auth_token": github_token or (env_config.auth_token if env_config else ""),
                "command": github_command or (env_config.command if env_config else ""),
                "args": github_args or (list(env_config.args) if env_config else []),
                "headers": env_config.headers if env_config else {},
                "toolsets": env_config.toolsets if env_config else (),
            }
        )
    return env_config


def normalize_github_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize GitHub tool result."""
    if result.get("is_error"):
        return {
            "source": "github",
            "available": False,
            "error": result.get("text") or "GitHub MCP tool call failed.",
            "tool": result.get("tool"),
            "arguments": result.get("arguments", {}),
        }
    return {
        "source": "github",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }
