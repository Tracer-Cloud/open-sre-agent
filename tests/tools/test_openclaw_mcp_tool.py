"""Tests for OpenClaw bridge function tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.tools.conftest import BaseToolContract, mock_agent_state
from tools.openclaw_mcp_tool import (
    call_openclaw_bridge_tool,
    get_openclaw_conversation,
    list_openclaw_bridge_tools,
    search_openclaw_conversations,
    send_openclaw_message,
)


class TestOpenClawListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_openclaw_bridge_tools.__opensre_registered_tool__


class TestOpenClawCallToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return call_openclaw_bridge_tool.__opensre_registered_tool__


class TestOpenClawConversationSearchToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_openclaw_conversations.__opensre_registered_tool__


class TestOpenClawConversationGetToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_openclaw_conversation.__opensre_registered_tool__


class TestOpenClawSendMessageToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return send_openclaw_message.__opensre_registered_tool__


def test_openclaw_tools_are_available_from_agent_state() -> None:
    sources = mock_agent_state(
        {
            "openclaw": {
                "connection_verified": True,
                "openclaw_mode": "stdio",
                "openclaw_command": "openclaw",
                "openclaw_args": ["mcp", "serve"],
                "openclaw_search_query": "checkout-api",
            }
        }
    )

    assert list_openclaw_bridge_tools.__opensre_registered_tool__.is_available(sources) is True
    assert call_openclaw_bridge_tool.__opensre_registered_tool__.is_available(sources) is True
    assert search_openclaw_conversations.__opensre_registered_tool__.is_available(sources) is True


def test_extract_params_maps_openclaw_source_fields() -> None:
    rt = call_openclaw_bridge_tool.__opensre_registered_tool__
    params = rt.extract_params(
        mock_agent_state(
            {
                "openclaw": {
                    "connection_verified": True,
                    "openclaw_mode": "stdio",
                    "openclaw_command": "openclaw",
                    "openclaw_args": ["mcp", "serve"],
                    "openclaw_token": "",
                    "openclaw_search_query": "checkout-api",
                }
            }
        )
    )

    assert params["openclaw_mode"] == "stdio"
    assert params["openclaw_command"] == "openclaw"
    assert params["openclaw_args"] == ["mcp", "serve"]


def test_extract_params_accept_plain_openclaw_config_keys() -> None:
    rt = call_openclaw_bridge_tool.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "openclaw": {
                "connection_verified": True,
                "url": "https://openclaw.example.com/mcp",
                "mode": "streamable-http",
                "auth_token": "tok",
                "command": "openclaw",
                "args": ["mcp", "serve"],
            }
        }
    )

    assert params["openclaw_url"] == "https://openclaw.example.com/mcp"
    assert params["openclaw_mode"] == "streamable-http"
    assert params["openclaw_token"] == "tok"
    assert params["openclaw_command"] == "openclaw"
    assert params["openclaw_args"] == ["mcp", "serve"]


def test_search_extract_params_maps_query() -> None:
    rt = search_openclaw_conversations.__opensre_registered_tool__
    params = rt.extract_params(
        mock_agent_state(
            {
                "openclaw": {
                    "connection_verified": True,
                    "openclaw_mode": "stdio",
                    "openclaw_command": "openclaw",
                    "openclaw_args": ["mcp", "serve"],
                    "openclaw_search_query": "checkout-api",
                }
            }
        )
    )

    assert params["search"] == "checkout-api"
    assert params["limit"] == 10


def test_get_conversation_extract_params_maps_conversation_id() -> None:
    rt = get_openclaw_conversation.__opensre_registered_tool__
    params = rt.extract_params(
        mock_agent_state(
            {
                "openclaw": {
                    "connection_verified": True,
                    "openclaw_mode": "stdio",
                    "openclaw_command": "openclaw",
                    "openclaw_args": ["mcp", "serve"],
                    "openclaw_conversation_id": "conv-123",
                }
            }
        )
    )

    assert params["conversation_id"] == "conv-123"


def test_list_openclaw_tools_returns_unavailable_without_config() -> None:
    with patch("tools.openclaw_mcp_tool.openclaw_config_from_env", return_value=None):
        result = list_openclaw_bridge_tools()

    assert result["available"] is False
    assert result["tools"] == []


def test_list_openclaw_tools_happy_path() -> None:
    mock_config = MagicMock()
    mock_config.mode = "stdio"
    mock_config.command = "openclaw"
    mock_config.url = ""

    with (
        patch("tools.openclaw_mcp_tool.openclaw_config_from_env", return_value=None),
        patch("tools.openclaw_mcp_tool.build_openclaw_config", return_value=mock_config),
        patch("tools.openclaw_mcp_tool.openclaw_runtime_unavailable_reason", return_value=None),
        patch(
            "tools.openclaw_mcp_tool.list_openclaw_mcp_tools",
            return_value=[{"name": "messages_read", "description": "", "input_schema": {}}],
        ),
    ):
        result = list_openclaw_bridge_tools(
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is True
    assert result["transport"] == "stdio"
    assert result["tools"][0]["name"] == "messages_read"
    # Listing is slimmed: schema dropped by default so it can't overflow context.
    assert "input_schema" not in result["tools"][0]
    assert result["total_tools"] == 1
    assert result["returned_tools"] == 1


def test_list_openclaw_tools_filters_by_name() -> None:
    mock_config = MagicMock()
    mock_config.mode = "stdio"
    mock_config.command = "openclaw"
    mock_config.url = ""

    with (
        patch("tools.openclaw_mcp_tool.openclaw_config_from_env", return_value=None),
        patch("tools.openclaw_mcp_tool.build_openclaw_config", return_value=mock_config),
        patch("tools.openclaw_mcp_tool.openclaw_runtime_unavailable_reason", return_value=None),
        patch(
            "tools.openclaw_mcp_tool.list_openclaw_mcp_tools",
            return_value=[
                {"name": "messages_read", "description": "Read", "input_schema": {}},
                {"name": "events_list", "description": "Events", "input_schema": {}},
            ],
        ),
    ):
        result = list_openclaw_bridge_tools(
            name_filter="events",
            openclaw_mode="stdio",
            openclaw_command="openclaw",
        )

    assert result["matched_tools"] == 1
    assert {t["name"] for t in result["tools"]} == {"events_list"}


def test_call_openclaw_tool_happy_path() -> None:
    mock_config = MagicMock()

    with (
        patch("tools.openclaw_mcp_tool.openclaw_config_from_env", return_value=None),
        patch("tools.openclaw_mcp_tool.build_openclaw_config", return_value=mock_config),
        patch("tools.openclaw_mcp_tool.openclaw_runtime_unavailable_reason", return_value=None),
        patch(
            "tools.openclaw_mcp_tool.invoke_openclaw_mcp_tool",
            return_value={
                "is_error": False,
                "tool": "messages_read",
                "arguments": {"session_key": "abc"},
                "text": "ok",
                "structured_content": [{"id": "msg-1"}],
                "content": [],
            },
        ),
    ):
        result = call_openclaw_bridge_tool(
            tool_name="messages_read",
            arguments={"session_key": "abc"},
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is True
    assert result["tool"] == "messages_read"
    assert result["structured_content"] == [{"id": "msg-1"}]


def test_call_openclaw_tool_returns_error_payload() -> None:
    mock_config = MagicMock()

    with (
        patch("tools.openclaw_mcp_tool.openclaw_config_from_env", return_value=None),
        patch("tools.openclaw_mcp_tool.build_openclaw_config", return_value=mock_config),
        patch("tools.openclaw_mcp_tool.openclaw_runtime_unavailable_reason", return_value=None),
        patch(
            "tools.openclaw_mcp_tool.invoke_openclaw_mcp_tool",
            return_value={
                "is_error": True,
                "tool": "messages_send",
                "arguments": {"session_key": "abc"},
                "text": "route missing",
            },
        ),
    ):
        result = call_openclaw_bridge_tool(
            tool_name="messages_send",
            arguments={"session_key": "abc"},
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is False
    assert "route missing" in result["error"]


def test_call_openclaw_tool_requires_tool_name() -> None:
    result = call_openclaw_bridge_tool(arguments={"session_key": "abc"})

    assert result["available"] is False
    assert "tool_name is required" in result["error"]


def test_search_openclaw_conversations_happy_path() -> None:
    mock_config = MagicMock()

    with (
        patch("tools.openclaw_mcp_tool.openclaw_config_from_env", return_value=None),
        patch("tools.openclaw_mcp_tool.build_openclaw_config", return_value=mock_config),
        patch("tools.openclaw_mcp_tool.openclaw_runtime_unavailable_reason", return_value=None),
        patch(
            "tools.openclaw_mcp_tool.invoke_openclaw_mcp_tool",
            return_value={
                "is_error": False,
                "tool": "conversations_list",
                "arguments": {"search": "checkout-api", "limit": 10},
                "text": "1 conversation",
                "structured_content": [{"session_key": "sess-1", "title": "Checkout debugging"}],
                "content": [],
            },
        ),
    ):
        result = search_openclaw_conversations(
            search="checkout-api",
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is True
    assert result["conversations"] == [{"session_key": "sess-1", "title": "Checkout debugging"}]


def test_get_openclaw_conversation_happy_path() -> None:
    mock_config = MagicMock()

    with (
        patch("tools.openclaw_mcp_tool.openclaw_config_from_env", return_value=None),
        patch("tools.openclaw_mcp_tool.build_openclaw_config", return_value=mock_config),
        patch("tools.openclaw_mcp_tool.openclaw_runtime_unavailable_reason", return_value=None),
        patch(
            "tools.openclaw_mcp_tool.invoke_openclaw_mcp_tool",
            return_value={
                "is_error": False,
                "tool": "conversations_get",
                "arguments": {"conversationId": "conv-1"},
                "text": "ok",
                "structured_content": {"id": "conv-1", "title": "Checkout debugging"},
                "content": [],
            },
        ),
    ):
        result = get_openclaw_conversation(
            conversation_id="conv-1",
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is True
    assert result["tool"] == "conversations_get"


def test_send_openclaw_message_happy_path() -> None:
    mock_config = MagicMock()

    with (
        patch("tools.openclaw_mcp_tool.openclaw_config_from_env", return_value=None),
        patch("tools.openclaw_mcp_tool.build_openclaw_config", return_value=mock_config),
        patch("tools.openclaw_mcp_tool.openclaw_runtime_unavailable_reason", return_value=None),
        patch(
            "tools.openclaw_mcp_tool.invoke_openclaw_mcp_tool",
            return_value={
                "is_error": False,
                "tool": "message_send",
                "arguments": {"conversationId": "conv-1", "content": "hello"},
                "text": "sent",
                "structured_content": {"ok": True},
                "content": [],
            },
        ),
    ):
        result = send_openclaw_message(
            conversation_id="conv-1",
            content="hello",
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is True
    assert result["tool"] == "message_send"
