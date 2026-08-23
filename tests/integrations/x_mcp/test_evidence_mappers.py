"""Evidence mapper coverage for the X MCP integration."""

from __future__ import annotations

import pytest

from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence


def test_list_x_tools_records_tool_inventory() -> None:
    """A non-empty tool listing becomes a citeable inventory entry."""
    evidence: dict[str, object] = {}
    tools = [
        {"name": "search-tweets", "description": "Search recent tweets"},
        {"name": "get-timeline", "description": "Read a user timeline"},
    ]

    merge_tool_evidence(
        evidence,
        "list_x_tools",
        {"available": True, "tools": tools, "total_tools": 2},
        {},
    )

    assert evidence["x_mcp_tools"] == tools
    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "x_mcp_tools",
            "label": "X MCP Tool Inventory",
            "summary": "Available X MCP tools",
            "url": None,
            "snippet": None,
        }
    ]


def test_call_x_tool_records_structured_result() -> None:
    """Structured X results survive the real merge path and become citeable."""
    evidence: dict[str, object] = {}
    output = {
        "available": True,
        "tool": "search-tweets",
        "text": "2 matching posts",
        "structured_content": {"tweets": [{"id": "1"}, {"id": "2"}]},
        "content": [],
    }

    merge_tool_evidence(
        evidence,
        "call_x_tool",
        output,
        {"tool_name": "search-tweets", "arguments": {"query": "outage"}},
    )

    assert evidence["x_mcp_results"] == [
        {
            "tool": "search-tweets",
            "text": "2 matching posts",
            "structured_content": {"tweets": [{"id": "1"}, {"id": "2"}]},
            "content": [],
        }
    ]
    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    assert entries[0]["source"] == "x_mcp_results"
    assert entries[0]["summary"] == "X MCP tool results"


def test_call_x_tool_records_numeric_structured_content() -> None:
    """Numeric IDs and metrics in structured MCP payloads remain reportable."""
    evidence: dict[str, object] = {}

    merge_tool_evidence(
        evidence,
        "call_x_tool",
        {
            "available": True,
            "tool": "search-tweets",
            "structured_content": {"tweets": [1, 2]},
        },
        {},
    )

    assert evidence["x_mcp_results"] == [
        {"tool": "search-tweets", "structured_content": {"tweets": [1, 2]}}
    ]
    assert evidence["catalog_entries"]


def test_repeated_x_calls_accumulate_without_duplicate_catalog_rows() -> None:
    """Repeated listings and calls retain evidence behind one catalog source each."""
    evidence: dict[str, object] = {}

    merge_tool_evidence(
        evidence,
        "list_x_tools",
        {"available": True, "tools": [{"name": "search-tweets"}]},
        {},
    )
    merge_tool_evidence(
        evidence,
        "list_x_tools",
        {
            "available": True,
            "tools": [
                {"name": "search-tweets", "input_schema": {"type": "object"}},
                {"name": "get-timeline"},
            ],
        },
        {},
    )
    for tool_name in ("search-tweets", "get-timeline"):
        merge_tool_evidence(
            evidence,
            "call_x_tool",
            {
                "available": True,
                "tool": tool_name,
                "text": f"{tool_name} result",
                "arguments": {"token": "must-not-enter-canonical-evidence"},
            },
            {"tool_name": tool_name},
        )

    assert evidence["x_mcp_tools"] == [
        {"name": "search-tweets", "input_schema": {"type": "object"}},
        {"name": "get-timeline"},
    ]
    results = evidence["x_mcp_results"]
    assert isinstance(results, list)
    assert [result["tool"] for result in results] == ["search-tweets", "get-timeline"]
    assert all("arguments" not in result for result in results)
    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    assert [entry["source"] for entry in entries] == ["x_mcp_tools", "x_mcp_results"]


def test_repeated_x_tool_listing_preserves_rich_descriptor_in_reverse_order() -> None:
    """A later compact listing must not erase schema metadata already collected."""
    evidence: dict[str, object] = {}
    rich_descriptor = {
        "name": "search-tweets",
        "description": "Search recent tweets",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    }

    merge_tool_evidence(
        evidence,
        "list_x_tools",
        {"available": True, "tools": [rich_descriptor]},
        {},
    )
    merge_tool_evidence(
        evidence,
        "list_x_tools",
        {"available": True, "tools": [{"name": "search-tweets"}]},
        {},
    )

    assert evidence["x_mcp_tools"] == [rich_descriptor]
    assert len(evidence["catalog_entries"]) == 1


@pytest.mark.parametrize(
    ("tool_name", "output"),
    [
        ("list_x_tools", {"available": True, "tools": []}),
        (
            "call_x_tool",
            {
                "available": True,
                "tool": "search-tweets",
                "text": "",
                "structured_content": None,
                "content": [],
            },
        ),
        (
            "call_x_tool",
            {
                "available": True,
                "text": False,
                "structured_content": {"items": [{}]},
                "content": [{}],
            },
        ),
        ("call_x_tool", {"available": False, "error": "permission denied"}),
    ],
)
def test_empty_or_unavailable_x_output_is_not_citeable(
    tool_name: str, output: dict[str, object]
) -> None:
    """Empty and failed MCP responses must not create report evidence."""
    evidence: dict[str, object] = {}

    merge_tool_evidence(evidence, tool_name, output, {})

    assert "catalog_entries" not in evidence
    assert "x_mcp_tools" not in evidence
    assert "x_mcp_results" not in evidence
