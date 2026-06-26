"""Tests for Sentry MCP function tools."""

from __future__ import annotations

from unittest.mock import patch

from tests.tools.conftest import BaseToolContract, mock_agent_state
from tools.sentry_mcp_tool import call_sentry_tool, list_sentry_tools


class TestSentryListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_sentry_tools.__opensre_registered_tool__


class TestSentryCallToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return call_sentry_tool.__opensre_registered_tool__


def test_tools_available_when_connection_verified() -> None:
    sources = mock_agent_state(
        {
            "sentry_mcp": {
                "connection_verified": True,
                "url": "https://mcp.sentry.dev/mcp",
                "mode": "streamable-http",
                "auth_token": "sntrytok_secret",
            }
        }
    )
    assert list_sentry_tools.__opensre_registered_tool__.is_available(sources) is True
    assert call_sentry_tool.__opensre_registered_tool__.is_available(sources) is True


def test_tools_unavailable_without_verification() -> None:
    sources = mock_agent_state({"sentry_mcp": {"connection_verified": False}})
    assert list_sentry_tools.__opensre_registered_tool__.is_available(sources) is False


def test_extract_params_maps_source_fields() -> None:
    rt = call_sentry_tool.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "sentry_mcp": {
                "connection_verified": True,
                "url": "https://mcp.sentry.dev/mcp",
                "mode": "streamable-http",
                "auth_token": "sntrytok_secret",
            }
        }
    )
    assert params["sentry_url"] == "https://mcp.sentry.dev/mcp"
    assert params["sentry_mode"] == "streamable-http"
    assert params["sentry_token"] == "sntrytok_secret"


def test_call_tool_requires_tool_name() -> None:
    result = call_sentry_tool(
        tool_name="",
        sentry_url="https://mcp.sentry.dev/mcp",
        sentry_token="sntrytok_secret",
    )
    assert result["available"] is False
    assert "tool_name is required" in str(result["error"])


def test_call_tool_unconfigured_returns_unavailable(monkeypatch) -> None:
    for var in (
        "SENTRY_MCP_MODE",
        "SENTRY_MCP_URL",
        "SENTRY_MCP_COMMAND",
        "SENTRY_MCP_AUTH_TOKEN",
        "SENTRY_MCP_ARGS",
    ):
        monkeypatch.delenv(var, raising=False)
    result = call_sentry_tool(tool_name="get_issue_details")
    assert result["available"] is False
    assert "not configured" in str(result["error"])


def test_call_tool_passes_through_result() -> None:
    fake_result = {
        "is_error": False,
        "text": "issue",
        "structured_content": {"id": "123"},
        "content": [],
        "tool": "get_issue_details",
        "arguments": {"issue_id": "123"},
    }
    with patch(
        "tools.sentry_mcp_tool.invoke_sentry_mcp_tool",
        return_value=fake_result,
    ) as mock_invoke:
        result = call_sentry_tool(
            tool_name="get_issue_details",
            arguments={"issue_id": "123"},
            sentry_url="https://mcp.sentry.dev/mcp",
            sentry_mode="streamable-http",
            sentry_token="sntrytok_secret",
        )
    mock_invoke.assert_called_once()
    assert result["available"] is True
    assert result["source"] == "sentry_mcp"
    assert result["structured_content"] == {"id": "123"}


def test_call_tool_surfaces_mcp_error() -> None:
    fake_result = {
        "is_error": True,
        "text": "permission denied",
        "tool": "update_issue",
        "arguments": {},
    }
    with patch(
        "tools.sentry_mcp_tool.invoke_sentry_mcp_tool",
        return_value=fake_result,
    ):
        result = call_sentry_tool(
            tool_name="update_issue",
            sentry_url="https://mcp.sentry.dev/mcp",
            sentry_token="sntrytok_secret",
        )
    assert result["available"] is False
    assert "permission denied" in str(result["error"])


def test_list_tools_returns_compact_summaries_without_schema() -> None:
    """Listing drops input_schema by default so the payload can't overflow the
    agent's context budget (mirrors list_posthog_tools)."""
    fake_tools = [
        {"name": "get_issue_details", "description": "Issue", "input_schema": {"a": 1}},
    ]
    with patch(
        "tools.sentry_mcp_tool.list_sentry_mcp_server_tools",
        return_value=fake_tools,
    ):
        result = list_sentry_tools(
            sentry_url="https://mcp.sentry.dev/mcp",
            sentry_mode="streamable-http",
            sentry_token="sntrytok_secret",
        )
    assert result["available"] is True
    assert result["transport"] == "streamable-http"
    assert result["total_tools"] == 1
    assert result["returned_tools"] == 1
    assert result["tools"] == [{"name": "get_issue_details", "description": "Issue"}]
    assert "input_schema" not in result["tools"][0]


def test_list_tools_filters_and_includes_schema_for_narrow_results() -> None:
    fake_tools = [
        {"name": "get_issue_details", "description": "Issue", "input_schema": {"q": "s"}},
        {"name": "search_events", "description": "Events", "input_schema": {"t": "s"}},
    ]
    with patch(
        "tools.sentry_mcp_tool.list_sentry_mcp_server_tools",
        return_value=fake_tools,
    ):
        result = list_sentry_tools(
            name_filter="issue",
            include_schema=True,
            sentry_url="https://mcp.sentry.dev/mcp",
            sentry_token="sntrytok_secret",
        )
    assert result["matched_tools"] == 1
    assert result["tools"][0]["name"] == "get_issue_details"
    assert result["tools"][0]["input_schema"] == {"q": "s"}
