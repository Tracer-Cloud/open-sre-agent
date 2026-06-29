"""Tests for GitHub helper credential mapping and MCP config resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.github_mcp import DEFAULT_GITHUB_MCP_MODE
from tools.utils.github_helpers import github_creds, resolve_github_mcp_config


def test_github_creds_maps_classified_integration_fields() -> None:
    creds = github_creds(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
            "command": "",
            "args": [],
        }
    )
    assert creds == {
        "github_url": "https://api.githubcopilot.com/mcp/",
        "github_mode": "streamable-http",
        "github_token": "ghp_test",
    }


def test_github_creds_prefers_legacy_tool_field_names() -> None:
    creds = github_creds(
        {
            "github_url": "http://github.example.com/mcp",
            "github_mode": "sse",
            "github_token": "legacy-token",
            "github_command": "gh-mcp",
            "github_args": ["--stdio"],
        }
    )
    assert creds == {
        "github_url": "http://github.example.com/mcp",
        "github_mode": "sse",
        "github_token": "legacy-token",
        "github_command": "gh-mcp",
        "github_args": ["--stdio"],
    }


def test_github_creds_omits_empty_defaults() -> None:
    assert github_creds({}) == {}


def test_resolve_github_mcp_config_uses_env_when_no_overrides() -> None:
    env_config = MagicMock()
    with patch("tools.utils.github_helpers.github_mcp_config_from_env", return_value=env_config):
        assert resolve_github_mcp_config(None, None, None) is env_config


def test_resolve_github_mcp_config_builds_when_token_present() -> None:
    env_config = MagicMock()
    built = MagicMock()
    with (
        patch("tools.utils.github_helpers.github_mcp_config_from_env", return_value=env_config),
        patch("tools.utils.github_helpers.build_github_mcp_config", return_value=built) as builder,
    ):
        result = resolve_github_mcp_config(None, None, "ghp_test")
    assert result is built
    builder.assert_called_once()
    payload = builder.call_args.args[0]
    assert payload["auth_token"] == "ghp_test"
    assert payload["mode"] == env_config.mode


def test_resolve_github_mcp_config_does_not_treat_default_mode_as_override() -> None:
    env_config = MagicMock()
    with patch("tools.utils.github_helpers.github_mcp_config_from_env", return_value=env_config):
        assert resolve_github_mcp_config(None, DEFAULT_GITHUB_MCP_MODE, None) is env_config
