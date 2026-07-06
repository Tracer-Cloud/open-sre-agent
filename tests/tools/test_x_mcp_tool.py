"""Tests for X (Twitter) MCP function tools."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.x_mcp.tools.x_mcp_tool import (
    _resolve_config,
    call_x_tool,
    list_x_tools,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestXListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_x_tools.__opensre_registered_tool__


class TestXCallToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return call_x_tool.__opensre_registered_tool__


_CONNECTION_PARAMS = frozenset({"x_url", "x_mode", "x_token", "x_command", "x_args"})


def test_connection_params_are_injected_not_model_supplied() -> None:
    """Regression: the LLM must not be able to supply connection/transport
    settings. Hallucinated values (e.g. mode="mcp" or a base URL without the
    ``/mcp`` path) previously overrode the verified config and broke calls, so
    these fields are injected from the verified integration and hidden from the
    model's tool schema.
    """
    for tool_fn in (list_x_tools, call_x_tool):
        rt = tool_fn.__opensre_registered_tool__
        assert set(rt.injected_params) >= _CONNECTION_PARAMS, (
            f"{rt.name} must inject connection params, not expose them to the model."
        )
        public_props = set(rt.public_input_schema.get("properties", {}))
        assert public_props.isdisjoint(_CONNECTION_PARAMS), (
            f"{rt.name} leaks connection params {public_props & _CONNECTION_PARAMS} "
            "into the model-facing schema."
        )


def test_call_tool_public_schema_exposes_only_tool_selection() -> None:
    rt = call_x_tool.__opensre_registered_tool__
    public_props = set(rt.public_input_schema.get("properties", {}))
    assert public_props == {"tool_name", "arguments"}
    assert rt.public_input_schema.get("required") == ["tool_name"]


def test_list_tool_public_schema_exposes_only_filter_controls() -> None:
    rt = list_x_tools.__opensre_registered_tool__
    public_props = set(rt.public_input_schema.get("properties", {}))
    assert public_props == {"name_filter", "include_schema"}
    assert public_props.isdisjoint(_CONNECTION_PARAMS)


def test_tools_available_when_connection_verified() -> None:
    sources = mock_agent_state(
        {
            "x_mcp": {
                "connection_verified": True,
                "url": "http://127.0.0.1:8000/mcp",
                "mode": "streamable-http",
            }
        }
    )
    assert list_x_tools.__opensre_registered_tool__.is_available(sources) is True
    assert call_x_tool.__opensre_registered_tool__.is_available(sources) is True


def test_tools_unavailable_without_verification() -> None:
    sources = mock_agent_state({"x_mcp": {"connection_verified": False}})
    assert list_x_tools.__opensre_registered_tool__.is_available(sources) is False


def test_extract_params_maps_source_fields() -> None:
    rt = call_x_tool.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "x_mcp": {
                "connection_verified": True,
                "url": "http://127.0.0.1:8000/mcp",
                "mode": "streamable-http",
            }
        }
    )
    assert params["x_url"] == "http://127.0.0.1:8000/mcp"
    assert params["x_mode"] == "streamable-http"


def test_call_tool_requires_tool_name() -> None:
    result = call_x_tool(
        tool_name="",
        x_url="http://127.0.0.1:8000/mcp",
    )
    assert result["available"] is False
    assert "tool_name is required" in str(result["error"])


def test_call_tool_unconfigured_returns_unavailable(monkeypatch) -> None:
    for var in ("X_MCP_MODE", "X_MCP_URL", "X_MCP_COMMAND", "X_MCP_AUTH_TOKEN", "X_MCP_ARGS"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("X_MCP_MODE", "stdio")
    result = call_x_tool(tool_name="search-tweets")
    assert result["available"] is False
    assert "not configured" in str(result["error"])


def test_call_tool_passes_through_result() -> None:
    fake_result = {
        "is_error": False,
        "text": "results",
        "structured_content": {"tweets": [1, 2]},
        "content": [],
        "tool": "search-tweets",
        "arguments": {"query": "outage"},
    }
    with patch(
        "integrations.x_mcp.tools.x_mcp_tool.invoke_x_mcp_tool",
        return_value=fake_result,
    ) as mock_invoke:
        result = call_x_tool(
            tool_name="search-tweets",
            arguments={"query": "outage"},
            x_url="http://127.0.0.1:8000/mcp",
            x_mode="streamable-http",
        )
    mock_invoke.assert_called_once()
    assert result["available"] is True
    assert result["source"] == "x_mcp"
    assert result["structured_content"] == {"tweets": [1, 2]}


def test_call_tool_surfaces_mcp_error() -> None:
    fake_result = {
        "is_error": True,
        "text": "permission denied",
        "tool": "post-tweet",
        "arguments": {},
    }
    with patch(
        "integrations.x_mcp.tools.x_mcp_tool.invoke_x_mcp_tool",
        return_value=fake_result,
    ):
        result = call_x_tool(
            tool_name="post-tweet",
            x_url="http://127.0.0.1:8000/mcp",
        )
    assert result["available"] is False
    assert "permission denied" in str(result["error"])


@pytest.mark.parametrize("guessed_mode", ["default", "mcp", "http", "bogus", ""])
def test_resolve_config_recovers_from_guessed_mode(guessed_mode: str) -> None:
    """The planner often guesses an invalid transport; fall back to HTTP."""
    config = _resolve_config(
        x_url="http://127.0.0.1:8000/mcp",
        x_mode=guessed_mode,
        x_token=None,
    )
    assert config is not None
    assert config.mode == "streamable-http"
    assert config.url == "http://127.0.0.1:8000/mcp"


def test_resolve_config_stdio_without_command_falls_back_to_http() -> None:
    """A 'stdio' request with only a URL must not build a broken stdio config."""
    config = _resolve_config(
        x_url="http://127.0.0.1:8000/mcp",
        x_mode="stdio",
        x_token=None,
        x_command="",
    )
    assert config is not None
    assert config.mode == "streamable-http"


def test_resolve_config_keeps_explicit_stdio_with_command() -> None:
    config = _resolve_config(
        x_url="",
        x_mode="stdio",
        x_token=None,
        x_command="python",
        x_args=["server.py"],
    )
    assert config is not None
    assert config.mode == "stdio"
    assert config.command == "python"


def test_list_tools_returns_compact_summaries_without_schema() -> None:
    fake_tools = [
        {"name": "search-tweets", "description": "Search tweets", "input_schema": {"a": 1}},
    ]
    with patch(
        "integrations.x_mcp.tools.x_mcp_tool.list_x_mcp_server_tools",
        return_value=fake_tools,
    ):
        result = list_x_tools(
            x_url="http://127.0.0.1:8000/mcp",
            x_mode="streamable-http",
        )
    assert result["available"] is True
    assert result["transport"] == "streamable-http"
    assert result["total_tools"] == 1
    assert result["returned_tools"] == 1
    assert result["tools"] == [{"name": "search-tweets", "description": "Search tweets"}]
    assert "input_schema" not in result["tools"][0]


def test_list_tools_filters_by_name_or_description() -> None:
    fake_tools = [
        {"name": "search-tweets", "description": "Search recent tweets", "input_schema": {}},
        {"name": "get-timeline", "description": "Get a user timeline", "input_schema": {}},
        {"name": "post-tweet", "description": "Create a tweet", "input_schema": {}},
    ]
    with patch(
        "integrations.x_mcp.tools.x_mcp_tool.list_x_mcp_server_tools",
        return_value=fake_tools,
    ):
        result = list_x_tools(
            name_filter="search timeline",
            x_url="http://127.0.0.1:8000/mcp",
        )
    names = {t["name"] for t in result["tools"]}
    assert names == {"search-tweets", "get-timeline"}
    assert result["matched_tools"] == 2
