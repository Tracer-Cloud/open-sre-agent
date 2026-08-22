"""GitHub MCP configuration resolution — merge env defaults with overrides."""

from __future__ import annotations

from integrations.github.mcp import (
    DEFAULT_GITHUB_MCP_MODE,
    GitHubMCPConfig,
    build_github_mcp_config,
    github_mcp_config_from_env,
)


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
    """Return the GitHub MCP config to use, merging env defaults with overrides.

    Reads ``github_mcp_config_from_env()`` for the env-derived baseline, then
    treats any non-default value among ``github_url``, ``github_token``,
    ``github_command``, ``github_args``, or a non-default ``github_mode`` as an
    explicit override. When no overrides are present, returns the env config
    as-is. Otherwise builds a fresh ``GitHubMCPConfig`` filling unset fields
    from the env config (or ``DEFAULT_GITHUB_MCP_MODE`` for ``mode`` when no
    env value is available) and returns it.
    """
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
