"""Unit tests for the X (Twitter) MCP integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from integrations.catalog import classify_integrations as _classify_integrations
from integrations.x_mcp import (
    DEFAULT_X_MCP_URL,
    XMCPConfig,
    build_x_mcp_config,
    describe_x_mcp_error,
    validate_x_mcp_config,
    x_mcp_config_from_env,
    x_mcp_runtime_unavailable_reason,
)

# ---------------------------------------------------------------------------
# XMCPConfig
# ---------------------------------------------------------------------------


class TestXMCPConfig:
    def test_defaults_to_local_streamable_http(self) -> None:
        config = XMCPConfig()
        assert config.mode == "streamable-http"
        assert config.url == DEFAULT_X_MCP_URL
        assert config.is_configured is True

    def test_streamable_http_requires_url(self) -> None:
        with pytest.raises(ValidationError, match="requires a non-empty url"):
            XMCPConfig(mode="streamable-http", url="")

    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValidationError, match="requires a non-empty command"):
            XMCPConfig(mode="stdio", command="", url="")

    def test_url_trailing_slash_stripped(self) -> None:
        config = XMCPConfig(url="http://127.0.0.1:8000/mcp/")
        assert config.url == "http://127.0.0.1:8000/mcp"

    def test_mode_mcp_alias_maps_to_streamable_http(self) -> None:
        config = XMCPConfig(mode="mcp")
        assert config.mode == "streamable-http"

    @pytest.mark.parametrize("alias", ["default", "http", "https", "streamable_http"])
    def test_mode_generic_aliases_map_to_streamable_http(self, alias: str) -> None:
        config = XMCPConfig(mode=alias)
        assert config.mode == "streamable-http"

    def test_bearer_prefix_stripped_from_auth_token(self) -> None:
        config = XMCPConfig(auth_token="Bearer secret")
        assert config.auth_token == "secret"

    def test_request_headers_include_auth_only_when_set(self) -> None:
        config = XMCPConfig(auth_token="secret")
        assert config.request_headers["Authorization"] == "Bearer secret"

    def test_request_headers_empty_without_auth_token(self) -> None:
        config = XMCPConfig()
        assert "Authorization" not in config.request_headers

    def test_subprocess_env_forwards_bearer_token(self) -> None:
        config = XMCPConfig(bearer_token="x_secret", mode="stdio", command="python")
        assert config.subprocess_env == {"X_BEARER_TOKEN": "x_secret"}

    def test_subprocess_env_empty_without_bearer_token(self) -> None:
        config = XMCPConfig()
        assert config.subprocess_env == {}


# ---------------------------------------------------------------------------
# Env loading / runtime gating
# ---------------------------------------------------------------------------


class TestEnvLoading:
    def test_returns_none_for_stdio_without_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X_MCP_MODE", "stdio")
        monkeypatch.delenv("X_MCP_COMMAND", raising=False)
        assert x_mcp_config_from_env() is None

    def test_loads_default_local_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("X_MCP_URL", "X_MCP_MODE", "X_MCP_AUTH_TOKEN", "X_BEARER_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        config = x_mcp_config_from_env()
        assert config is not None
        assert config.url == DEFAULT_X_MCP_URL
        assert config.mode == "streamable-http"

    def test_runtime_reason_ok_for_http_without_token(self) -> None:
        config = build_x_mcp_config({"url": DEFAULT_X_MCP_URL})
        assert x_mcp_runtime_unavailable_reason(config) is None

    def test_runtime_reason_requires_bearer_token_for_stdio(self) -> None:
        config = build_x_mcp_config({"mode": "stdio", "command": "python", "bearer_token": ""})
        reason = x_mcp_runtime_unavailable_reason(config)
        assert reason is not None
        assert "bearer token" in reason


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validation_passes_when_tools_listed(self) -> None:
        config = build_x_mcp_config({"url": DEFAULT_X_MCP_URL})
        fake_tools = [
            {"name": "search-tweets", "description": "Search tweets", "input_schema": {}},
            {"name": "get-timeline", "description": "Get a user timeline", "input_schema": {}},
        ]
        with patch(
            "integrations.x_mcp.list_x_mcp_tools",
            return_value=fake_tools,
        ):
            result = validate_x_mcp_config(config)
        assert result.ok is True
        assert result.tool_names == ("get-timeline", "search-tweets")
        assert "discovered 2 tool(s)" in result.detail

    def test_validation_fails_when_no_tools(self) -> None:
        config = build_x_mcp_config({"url": DEFAULT_X_MCP_URL})
        with patch(
            "integrations.x_mcp.list_x_mcp_tools",
            return_value=[],
        ):
            result = validate_x_mcp_config(config)
        assert result.ok is False
        assert "no tools" in result.detail

    def test_validation_handles_exception(self) -> None:
        config = build_x_mcp_config({"url": DEFAULT_X_MCP_URL})
        with patch(
            "integrations.x_mcp.list_x_mcp_tools",
            side_effect=RuntimeError("boom"),
        ):
            result = validate_x_mcp_config(config)
        assert result.ok is False
        assert "validation failed" in result.detail

    def test_validation_fails_for_stdio_without_bearer_token(self) -> None:
        config = build_x_mcp_config({"mode": "stdio", "command": "python"})
        result = validate_x_mcp_config(config)
        assert result.ok is False
        assert "bearer token" in result.detail


def test_describe_error_includes_connect_hint() -> None:
    import httpx

    config = build_x_mcp_config({"url": DEFAULT_X_MCP_URL})
    err = httpx.ConnectError("connection refused")
    detail = describe_x_mcp_error(err, config)
    assert "Could not reach" in detail


# ---------------------------------------------------------------------------
# Catalog classification
# ---------------------------------------------------------------------------


def test_classify_x_mcp_credentials() -> None:
    records = [
        {
            "id": "x-mcp-local",
            "service": "x_mcp",
            "status": "active",
            "credentials": {
                "url": "http://127.0.0.1:8000/mcp",
                "mode": "streamable-http",
            },
        }
    ]
    from tools.investigation.stages.gather_evidence.tools import availability_view

    resolved = _classify_integrations(records)
    assert "x_mcp" in resolved
    assert resolved["x_mcp"].url == "http://127.0.0.1:8000/mcp"
    # connection_verified is set at the tool-availability boundary
    view = availability_view(resolved)
    assert view["x_mcp"]["connection_verified"] is True
