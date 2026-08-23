"""Tests for interactive-shell gather persistence."""

from __future__ import annotations

from core.agent_harness.session import InMemorySessionStore
from surfaces.interactive_shell.runtime.integration_tool_gathering import _persist_tool_calls
from surfaces.interactive_shell.session import Session


def test_persist_tool_calls_records_failed_result(monkeypatch) -> None:
    storage = InMemorySessionStore()
    session = Session(store=storage)
    storage.open_session(session)

    monkeypatch.setattr(
        "core.agent_harness.spi.defaults.default_session_store",
        lambda: storage,
    )

    _persist_tool_calls(
        session,
        [
            (
                "test_tool",
                {"query": "something"},
                {"error": "boom"},
            )
        ],
    )

    records = storage.read(session.session_id)
    tool_call = next(record for record in records if record["type"] == "tool_call")
    tool_result = next(record for record in records if record["type"] == "tool_result")

    assert tool_call["tool"] == "test_tool"
    assert tool_call["arguments"] == {"query": "something"}
    assert tool_result["tool"] == "test_tool"
    assert tool_result["content"] == '{"error": "boom"}'
    assert tool_result["ok"] is False
