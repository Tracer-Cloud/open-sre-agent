"""Pin McpTransportMode members and their round-trip from string."""

from __future__ import annotations

from integrations.mcp_transport import McpTransportMode


def test_mcp_transport_mode_members_are_stable() -> None:
    assert [mode.value for mode in McpTransportMode] == ["stdio", "sse", "streamable-http"]


def test_mcp_transport_mode_round_trips_from_string() -> None:
    """Config/JSON persist these as plain strings; StrEnum must accept them back."""
    for mode in McpTransportMode:
        assert McpTransportMode(mode.value) is mode


def test_mcp_transport_mode_keeps_hyphenated_streamable_http_value() -> None:
    """Config/JSON already depend on the hyphenated spelling."""
    assert McpTransportMode.STREAMABLE_HTTP.value == "streamable-http"
    assert McpTransportMode("streamable-http") is McpTransportMode.STREAMABLE_HTTP
