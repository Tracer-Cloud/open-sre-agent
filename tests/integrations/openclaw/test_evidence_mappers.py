"""Evidence mapper coverage for the OpenClaw read tools."""

from __future__ import annotations

from integrations.openclaw.tools.openclaw_mcp_tool.evidence import (
    map_call_openclaw_tool,
    map_get_openclaw_conversation,
    map_list_openclaw_tools,
    map_search_openclaw_conversations,
)


class TestMapListOpenclawTools:
    def test_records_entry_when_tools_present(self) -> None:
        evidence: dict = {}

        map_list_openclaw_tools(
            evidence,
            {
                "available": True,
                "tools": [{"name": "messages_read"}, {"name": "events_list"}],
                "returned_tools": 2,
                "total_tools": 12,
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "list_openclaw_tools"
        assert entries[0]["label"] == "OpenClaw Tools"
        assert entries[0]["summary"] == "2 tools listed (12 total)"
        assert "messages_read" not in (entries[0]["summary"] or "")

    def test_records_nothing_when_listing_is_empty(self) -> None:
        evidence: dict = {}

        map_list_openclaw_tools(evidence, {"available": True, "tools": []}, {})
        map_list_openclaw_tools(evidence, {}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict = {}

        map_list_openclaw_tools(
            evidence,
            {
                "available": False,
                "error": "OpenClaw MCP integration is not configured.",
                "tools": [],
            },
            {},
        )

        assert "catalog_entries" not in evidence


class TestMapSearchOpenclawConversations:
    def test_records_entry_when_conversations_present(self) -> None:
        evidence: dict = {}

        map_search_openclaw_conversations(
            evidence,
            {
                "available": True,
                "search": "checkout-api",
                "conversations": [{"session_key": "sess-1", "title": "Checkout debugging"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "search_openclaw_conversations"
        assert entries[0]["summary"] == "1 conversation matching 'checkout-api'"

    def test_records_nothing_when_no_conversations(self) -> None:
        evidence: dict = {}

        map_search_openclaw_conversations(evidence, {"available": True, "conversations": []}, {})
        map_search_openclaw_conversations(evidence, {}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict = {}

        map_search_openclaw_conversations(
            evidence,
            {
                "available": False,
                "error": "OpenClaw MCP integration is not configured.",
                "conversations": [],
            },
            {},
        )

        assert "catalog_entries" not in evidence


class TestMapGetOpenclawConversation:
    def test_records_entry_with_id_and_title(self) -> None:
        evidence: dict = {}

        map_get_openclaw_conversation(
            evidence,
            {
                "available": True,
                "tool": "conversations_get",
                "text": "ok",
                "structured_content": {"id": "conv-1", "title": "Checkout debugging"},
            },
            {"conversation_id": "conv-1"},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_openclaw_conversation"
        assert entries[0]["summary"] == "conv-1 — Checkout debugging"
        assert "ok" not in (entries[0]["summary"] or "")

    def test_records_nothing_when_payload_is_empty(self) -> None:
        evidence: dict = {}

        map_get_openclaw_conversation(
            evidence, {"available": True, "structured_content": {}, "text": ""}, {}
        )
        map_get_openclaw_conversation(evidence, {}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict = {}

        map_get_openclaw_conversation(
            evidence, {"available": False, "error": "conversation_id is required."}, {}
        )

        assert "catalog_entries" not in evidence


class TestMapCallOpenclawTool:
    def test_records_entry_for_structured_result(self) -> None:
        evidence: dict = {}

        map_call_openclaw_tool(
            evidence,
            {
                "available": True,
                "tool": "messages_read",
                "text": "a long transcript that must not be inlined into the catalog",
                "structured_content": [{"id": "msg-1"}, {"id": "msg-2"}],
                "content": [],
            },
            {"tool_name": "messages_read"},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "call_openclaw_tool"
        assert entries[0]["summary"] == "messages_read returned 2 structured items"
        assert "transcript" not in (entries[0]["summary"] or "")

    def test_records_nothing_when_payload_is_empty(self) -> None:
        evidence: dict = {}

        map_call_openclaw_tool(
            evidence,
            {
                "available": True,
                "tool": "messages_read",
                "text": "",
                "structured_content": None,
                "content": [],
            },
            {},
        )
        map_call_openclaw_tool(evidence, {}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict = {}

        map_call_openclaw_tool(
            evidence,
            {"available": False, "error": "tool_name is required to call an OpenClaw MCP tool."},
            {},
        )

        assert "catalog_entries" not in evidence
