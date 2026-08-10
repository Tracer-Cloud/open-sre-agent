"""Tests for TurnSnapshot active_tools population and has_active_tool method."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from core.agent_harness.turns.action_driver import _build_action_agent
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


class MockTool:
    """Mock tool for testing."""

    def __init__(self, name: str):
        self.name = name


def _mock_session() -> Any:
    """Build a mock session for testing."""
    return type(
        "MockSession",
        (),
        {
            "cli_agent_messages": [],
            "configured_integrations_known": True,
            "configured_integrations": (),
            "last_state": None,
            "last_synthetic_observation_path": None,
            "reasoning_effort": None,
            "select_turn_runtime_input": None,
        },
    )()


def test_gateway_shaped_tool_list_gets_the_override():
    """Test gateway-shaped tool list (without investigation_start) gets dispatch override."""
    session = _mock_session()
    agent_tools = [MockTool("shell_run"), MockTool("assistant_handoff")]

    deps = type("Deps", (), {"llm_factory": None})()

    result = _build_action_agent(
        message="test message",
        session=session,
        agent_tools=agent_tools,
        turn_snapshot=None,  # This forces creation of a new snapshot
        resolved_integrations={},
        deps=deps,
        tool_hooks=None,
        tool_resources={},
        observer=MagicMock(),
    )

    assert "INVESTIGATION DISPATCH (this surface, this turn)" in result.system


def test_repl_shaped_tool_list_keeps_the_mandate():
    """Test REPL-shaped tool list (with investigation_start) keeps the mandate."""
    session = _mock_session()
    agent_tools = [MockTool("shell_run"), MockTool("investigation_start")]

    deps = type("Deps", (), {"llm_factory": None})()

    result = _build_action_agent(
        message="test message",
        session=session,
        agent_tools=agent_tools,
        turn_snapshot=None,  # This forces creation of a new snapshot
        resolved_integrations={},
        deps=deps,
        tool_hooks=None,
        tool_resources={},
        observer=MagicMock(),
    )

    assert "INVESTIGATION DISPATCH (this surface, this turn)" not in result.system
    assert "investigation_start ALWAYS (even when this line is none)" in result.system


def test_has_active_tool_method():
    """Test the has_active_tool method works correctly."""
    tool1 = MockTool("investigation_start")
    tool2 = MockTool("other_tool")

    snapshot = TurnSnapshot(
        text="test",
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
        active_tools=(tool1, tool2),
    )

    assert snapshot.has_active_tool("investigation_start") is True
    assert snapshot.has_active_tool("other_tool") is True
    assert snapshot.has_active_tool("missing_tool") is False


def test_has_active_tool_with_empty_tools():
    """Test has_active_tool returns False when active_tools is empty."""
    snapshot = TurnSnapshot(
        text="test",
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
        active_tools=(),
    )

    assert snapshot.has_active_tool("investigation_start") is False


def test_has_active_tool_with_no_name_attribute():
    """Test has_active_tool handles tools without name attribute gracefully."""
    tool_without_name = type("ToolWithoutName", (), {})()

    snapshot = TurnSnapshot(
        text="test",
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
        active_tools=(tool_without_name,),
    )

    # Should return False without error
    assert snapshot.has_active_tool("investigation_start") is False
