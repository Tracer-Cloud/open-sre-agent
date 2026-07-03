"""Helper functions for GitHub tools."""

from __future__ import annotations

from typing import Any

from integrations.github.mcp import (
    DEFAULT_GITHUB_MCP_MODE,
    GitHubMCPConfig,
    build_github_mcp_config,
    github_mcp_config_from_env,
)


def github_source_available(sources: dict[str, dict]) -> bool:
    """Check if source is available."""
    return bool(sources.get("github", {}).get("connection_verified"))


def github_creds(gh: dict) -> dict[str, Any]:
    """Map classified GitHub integration fields to tool credential kwargs."""
    creds: dict[str, Any] = {}
    url = gh.get("github_url") or gh.get("url")
    if url:
        creds["github_url"] = url
    mode = gh.get("github_mode") or gh.get("mode")
    if mode:
        creds["github_mode"] = mode
    token = gh.get("github_token") or gh.get("auth_token")
    if token:
        creds["github_token"] = token
    command = gh.get("github_command") or gh.get("command")
    if command:
        creds["github_command"] = command
    args = gh.get("github_args")
    if args is None:
        args = gh.get("args")
    if args:
        creds["github_args"] = list(args)
    return creds


def _has_explicit_github_mcp_overrides(
    github_url: str | None,
    github_mode: str | None,
    github_token: str | None,
    github_command: str | None,
    github_args: list[str] | None,
) -> bool:
    if github_url or github_token or github_command or github_args:
        return True
    return bool(github_mode and github_mode != DEFAULT_GITHUB_MCP_MODE)


def resolve_github_mcp_config(
    github_url: str | None,
    github_mode: str | None,
    github_token: str | None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
) -> GitHubMCPConfig | None:
    """Resolve GitHub MCP config."""
    env_config = github_mcp_config_from_env()
    if not _has_explicit_github_mcp_overrides(
        github_url, github_mode, github_token, github_command, github_args
    ):
        return env_config
    return build_github_mcp_config(
        {
            "url": github_url or (env_config.url if env_config else ""),
            "mode": github_mode or (env_config.mode if env_config else DEFAULT_GITHUB_MCP_MODE),
            "auth_token": github_token or (env_config.auth_token if env_config else ""),
            "command": github_command or (env_config.command if env_config else ""),
            "args": github_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
            "toolsets": env_config.toolsets if env_config else (),
        }
    )


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
