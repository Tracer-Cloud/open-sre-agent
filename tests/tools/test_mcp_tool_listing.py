"""Tests for the shared MCP tool-catalog listing helper."""

from __future__ import annotations

from tools.utils.mcp_tool_listing import (
    MAX_DESCRIPTION_CHARS,
    MAX_SCHEMAS_RETURNED,
    MAX_TOOLS_RETURNED,
    build_mcp_tool_listing,
)


def _tools(count: int) -> list[dict[str, object]]:
    return [
        {"name": f"tool-{i:03d}", "description": f"desc {i}", "input_schema": {"i": i}}
        for i in range(count)
    ]


def test_default_listing_drops_schema_and_truncates_description() -> None:
    tools = [{"name": "execute-sql", "description": "Run HogQL " * 50, "input_schema": {"a": 1}}]
    listing = build_mcp_tool_listing(tools, name_filter=None, include_schema=False)

    entry = listing["tools"][0]
    assert entry["name"] == "execute-sql"
    assert "input_schema" not in entry
    assert len(entry["description"]) <= MAX_DESCRIPTION_CHARS
    assert listing["total_tools"] == 1
    assert listing["returned_tools"] == 1


def test_caps_returned_tools_and_notes_truncation() -> None:
    listing = build_mcp_tool_listing(_tools(244), name_filter=None, include_schema=False)
    assert listing["total_tools"] == 244
    assert listing["returned_tools"] == MAX_TOOLS_RETURNED
    assert "name_filter" in str(listing.get("notes", ""))


def test_name_filter_matches_name_or_description() -> None:
    tools = [
        {"name": "execute-sql", "description": "Run HogQL", "input_schema": {}},
        {"name": "feature-flag-get-all", "description": "List flags", "input_schema": {}},
        {"name": "query-trends", "description": "Trend query over events", "input_schema": {}},
    ]
    listing = build_mcp_tool_listing(tools, name_filter="events query sql", include_schema=False)
    assert {t["name"] for t in listing["tools"]} == {"execute-sql", "query-trends"}
    assert listing["matched_tools"] == 2
    assert listing["name_filter"] == "events query sql"


def test_include_schema_only_when_result_set_is_small() -> None:
    narrow = build_mcp_tool_listing(_tools(2), name_filter=None, include_schema=True)
    assert all("input_schema" in t for t in narrow["tools"])

    wide = build_mcp_tool_listing(
        _tools(MAX_SCHEMAS_RETURNED + 1), name_filter=None, include_schema=True
    )
    assert all("input_schema" not in t for t in wide["tools"])
    assert "input_schema omitted" in str(wide.get("notes", ""))


def test_filter_example_appears_in_notes() -> None:
    # The vendor-specific example only shows when a filter narrowed the set below
    # the cap while the full catalog still exceeds it.
    listing = build_mcp_tool_listing(
        _tools(100),
        name_filter="tool-01",
        include_schema=False,
        filter_example="issue event trace",
    )
    assert "issue event trace" in str(listing.get("notes", ""))


def test_skips_unnamed_or_non_dict_entries_gracefully() -> None:
    # Defensive: the helper is fed whatever the MCP server returns.
    listing = build_mcp_tool_listing(
        [{"name": "ok", "description": "d", "input_schema": {}}],
        name_filter=None,
        include_schema=False,
    )
    assert listing["returned_tools"] == 1
