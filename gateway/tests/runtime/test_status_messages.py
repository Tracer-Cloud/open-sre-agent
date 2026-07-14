"""Tests for Telegram gateway status copy."""

from __future__ import annotations

from core.tool_framework.registered_tool import RegisteredTool
from gateway.runtime.status_messages import (
    INITIAL_STATUSES,
    _tool_label,
    initial_status_message,
    normalize_gateway_status,
    status_from_response_label,
    status_from_tool_start,
)


def _make_tool(name: str, description: str) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}, "required": []},
        source="datadog",
        run=lambda **_kwargs: {},
    )


def _register(monkeypatch, tools: list[RegisteredTool]) -> None:
    monkeypatch.setattr("tools.registry.get_registered_tools", lambda: tools)
    _tool_label.cache_clear()


def test_initial_status_message_is_non_empty() -> None:
    assert initial_status_message().strip()
    assert initial_status_message() != "Working…"


def test_normalize_gateway_status_rejects_working_placeholder() -> None:
    for banned in ("Working…", "Working...", "working", "  Working  ", ""):
        assert normalize_gateway_status(banned) in INITIAL_STATUSES
    assert normalize_gateway_status("keep this") == "keep this"


def test_response_label_maps_working_to_initial_status() -> None:
    assert status_from_response_label("working") in INITIAL_STATUSES
    assert status_from_response_label("") in INITIAL_STATUSES


def test_response_label_maps_assistant() -> None:
    assert status_from_response_label("assistant") == "💬 Composing your reply…"


def test_response_label_falls_back_for_unknown_label() -> None:
    assert status_from_response_label("gather") == "✨ gather…"


def test_tool_status_uses_registry_description(monkeypatch) -> None:
    tool = _make_tool(
        "query_datadog_monitors",
        "Query Datadog monitors for alert configuration and state.",
    )
    _register(monkeypatch, [tool])

    assert (
        status_from_tool_start("query_datadog_monitors")
        == "⏳ Query Datadog monitors for alert configuration and state…"
    )


def test_tool_status_falls_back_to_humanized_name(monkeypatch) -> None:
    _register(monkeypatch, [])

    assert status_from_tool_start("list_open_pull_requests") == "⏳ list open pull requests…"


def test_tool_status_includes_first_input_hint(monkeypatch) -> None:
    tool = _make_tool("slash_invoke", "Run a registered interactive-shell slash command.")
    _register(monkeypatch, [tool])

    status = status_from_tool_start(
        "slash_invoke",
        {"command": "/integrations", "args": ["verify", "telegram"]},
    )
    assert status.startswith("⏳ Run a registered interactive-shell slash command…")
    assert "/integrations" in status
