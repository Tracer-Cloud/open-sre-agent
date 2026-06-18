"""Tests for PostHog MCP function tools."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.PostHogMCPTool import call_posthog_tool, list_posthog_tools
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestPostHogListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_posthog_tools.__opensre_registered_tool__


class TestPostHogCallToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return call_posthog_tool.__opensre_registered_tool__


_CONNECTION_PARAMS = frozenset(
    {
        "posthog_url",
        "posthog_mode",
        "posthog_token",
        "posthog_command",
        "posthog_args",
    }
)


def test_connection_params_are_injected_not_model_supplied() -> None:
    """Regression: the LLM must not be able to supply connection/transport
    settings. Hallucinated values (e.g. mode="mcp" or a base URL without the
    ``/mcp`` path) previously overrode the verified config and broke calls, so
    these fields are injected from the verified integration and hidden from the
    model's tool schema.
    """
    for tool_fn in (list_posthog_tools, call_posthog_tool):
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
    rt = call_posthog_tool.__opensre_registered_tool__
    public_props = set(rt.public_input_schema.get("properties", {}))
    assert public_props == {"tool_name", "arguments"}
    assert rt.public_input_schema.get("required") == ["tool_name"]


def test_list_tool_public_schema_takes_no_model_args() -> None:
    rt = list_posthog_tools.__opensre_registered_tool__
    assert set(rt.public_input_schema.get("properties", {})) == set()


def test_validate_public_input_rejects_model_supplied_connection_params() -> None:
    rt = call_posthog_tool.__opensre_registered_tool__
    # tool_name only is the valid model-facing shape.
    assert rt.validate_public_input({"tool_name": "query-run"}) is None


def test_tools_available_when_connection_verified() -> None:
    sources = mock_agent_state(
        {
            "posthog_mcp": {
                "connection_verified": True,
                "url": "https://mcp.posthog.com/mcp",
                "mode": "streamable-http",
                "auth_token": "phx_secret",
            }
        }
    )
    assert list_posthog_tools.__opensre_registered_tool__.is_available(sources) is True
    assert call_posthog_tool.__opensre_registered_tool__.is_available(sources) is True


def test_tools_unavailable_without_verification() -> None:
    sources = mock_agent_state({"posthog_mcp": {"connection_verified": False}})
    assert list_posthog_tools.__opensre_registered_tool__.is_available(sources) is False


def test_extract_params_maps_source_fields() -> None:
    rt = call_posthog_tool.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "posthog_mcp": {
                "connection_verified": True,
                "url": "https://mcp.posthog.com/mcp",
                "mode": "streamable-http",
                "auth_token": "phx_secret",
            }
        }
    )
    assert params["posthog_url"] == "https://mcp.posthog.com/mcp"
    assert params["posthog_mode"] == "streamable-http"
    assert params["posthog_token"] == "phx_secret"


def test_call_tool_requires_tool_name() -> None:
    result = call_posthog_tool(
        tool_name="",
        posthog_url="https://mcp.posthog.com/mcp",
        posthog_token="phx_secret",
    )
    assert result["available"] is False
    assert "tool_name is required" in str(result["error"])


def test_call_tool_unconfigured_returns_unavailable(monkeypatch) -> None:
    for var in (
        "POSTHOG_MCP_MODE",
        "POSTHOG_MCP_URL",
        "POSTHOG_MCP_COMMAND",
        "POSTHOG_MCP_AUTH_TOKEN",
        "POSTHOG_MCP_ARGS",
    ):
        monkeypatch.delenv(var, raising=False)
    result = call_posthog_tool(tool_name="query-run")
    assert result["available"] is False
    assert "not configured" in str(result["error"])


def test_call_tool_passes_through_result() -> None:
    fake_result = {
        "is_error": False,
        "text": "rows",
        "structured_content": {"results": [1, 2]},
        "content": [],
        "tool": "query-run",
        "arguments": {"query": "SELECT 1"},
    }
    with patch(
        "app.tools.PostHogMCPTool.invoke_posthog_mcp_tool",
        return_value=fake_result,
    ) as mock_invoke:
        result = call_posthog_tool(
            tool_name="query-run",
            arguments={"query": "SELECT 1"},
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_mode="streamable-http",
            posthog_token="phx_secret",
        )
    mock_invoke.assert_called_once()
    assert result["available"] is True
    assert result["source"] == "posthog_mcp"
    assert result["structured_content"] == {"results": [1, 2]}


def test_call_tool_surfaces_mcp_error() -> None:
    fake_result = {
        "is_error": True,
        "text": "permission denied",
        "tool": "feature-flag-create",
        "arguments": {},
    }
    with patch(
        "app.tools.PostHogMCPTool.invoke_posthog_mcp_tool",
        return_value=fake_result,
    ):
        result = call_posthog_tool(
            tool_name="feature-flag-create",
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_token="phx_secret",
        )
    assert result["available"] is False
    assert "permission denied" in str(result["error"])


def test_list_tools_returns_discovered_tools() -> None:
    fake_tools = [
        {"name": "query-run", "description": "Run HogQL", "input_schema": {}},
    ]
    with patch(
        "app.tools.PostHogMCPTool.list_posthog_mcp_server_tools",
        return_value=fake_tools,
    ):
        result = list_posthog_tools(
            posthog_url="https://mcp.posthog.com/mcp",
            posthog_mode="streamable-http",
            posthog_token="phx_secret",
        )
    assert result["available"] is True
    assert result["tools"] == fake_tools
    assert result["transport"] == "streamable-http"
