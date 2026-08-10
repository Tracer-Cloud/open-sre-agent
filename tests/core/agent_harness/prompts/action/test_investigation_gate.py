"""Tests for investigation dispatch gate in action prompts."""

from __future__ import annotations

from typing import Any

from core.agent_harness.prompts.action.assemble import (
    build_action_system_prompt_envelope,
    connected_integrations_block,
    investigation_dispatch_blocks,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


class MockTool:
    """Mock tool for testing."""

    def __init__(self, name: str):
        self.name = name


def _mock_snapshot_base() -> dict[str, Any]:
    """Base snapshot fields for testing."""
    return {
        "text": "test message",
        "conversation_messages": (),
        "configured_integrations": ("datadog", "grafana"),
        "configured_integrations_known": True,
        "last_state": None,
        "last_synthetic_observation_path": None,
        "reasoning_effort": None,
        "setup_state": "",
    }


def test_gate_note_mandates_investigation_start_when_tool_present():
    """Test gate note mandates investigation_start when tool is present."""
    tools = (MockTool("investigation_start"), MockTool("other_tool"))
    snapshot = TurnSnapshot(active_tools=tools, **_mock_snapshot_base())

    result = connected_integrations_block(snapshot)

    # Should contain the mandate text
    assert "investigation_start ALWAYS" in result
    assert "INVESTIGATION DISPATCH below" not in result


def test_gate_note_is_byte_identical_when_tool_present():
    """Test gate note is byte-identical to expected when tool is present."""
    tools = (MockTool("investigation_start"), MockTool("other_tool"))
    snapshot = TurnSnapshot(active_tools=tools, **_mock_snapshot_base())

    result = connected_integrations_block(snapshot)

    # Should contain the exact available-case string (inlined literal)
    expected_literal = (
        "This listing does NOT gate diagnostic→investigation. Cause/why / "
        "figure-out questions → assistant_handoff + gather + Want-me-to "
        "investigate offer. Explicit investigate/RCA/diagnose/analyze/"
        "root-cause verbs → investigation_start ALWAYS (even when this line "
        "is none).\n"
    )
    assert expected_literal in result


def test_dispatch_block_absent_when_active_tools_unknown():
    """Test dispatch blocks returns empty when active_tools is unknown (empty)."""
    snapshot = TurnSnapshot(active_tools=(), **_mock_snapshot_base())

    result = investigation_dispatch_blocks(snapshot)

    assert result == ()


def test_dispatch_block_emitted_when_investigation_start_missing():
    """Test dispatch blocks returns block when investigation_start is missing."""
    tools = (MockTool("other_tool"), MockTool("yet_another"))
    snapshot = TurnSnapshot(active_tools=tools, **_mock_snapshot_base())

    result = investigation_dispatch_blocks(snapshot)

    assert len(result) == 1
    block = result[0]
    assert block.id == "investigation-dispatch"
    assert "investigation_start and alert_sample are NOT in your tool list" in block.content
    assert 'slash_invoke(command="/investigate")' in block.content


def test_dispatch_block_forbids_slash_investigate_substitution():
    """Test dispatch block specifically forbids slash_invoke substitution."""
    tools = (MockTool("other_tool"),)
    snapshot = TurnSnapshot(active_tools=tools, **_mock_snapshot_base())

    result = investigation_dispatch_blocks(snapshot)

    assert len(result) == 1
    block = result[0]
    # Should forbid the actually-observed failure pattern
    assert 'Do NOT substitute slash_invoke(command="/investigate")' in block.content
    assert "shell_run with `opensre investigate`" in block.content
    assert "pipeline runs for minutes" in block.content


def test_dispatch_block_renders_in_cached_half():
    """Test dispatch block uses CONTEXT tier for render_cached."""
    tools = (MockTool("other_tool"),)
    snapshot = TurnSnapshot(active_tools=tools, **_mock_snapshot_base())

    envelope = build_action_system_prompt_envelope(snapshot)
    cached = envelope.render_cached()
    ephemeral = envelope.render_ephemeral()

    # The dispatch block should be in the cached half - check specific text
    assert "INVESTIGATION DISPATCH (this surface, this turn)" in cached
    assert "INVESTIGATION DISPATCH (this surface, this turn)" not in ephemeral

    # Also verify block order by checking ids
    block_ids = [b.id for b in envelope.blocks]
    connected_idx = block_ids.index("connected-integrations")
    dispatch_idx = block_ids.index("investigation-dispatch")
    assert dispatch_idx == connected_idx + 1


def test_base_system_prompt_is_unmodified():
    """Test that the base system prompt in text.py is not modified."""
    from core.agent_harness.prompts.action.text import _SYSTEM_PROMPT_BASE

    # Should not contain investigation dispatch text
    assert "INVESTIGATION DISPATCH" not in _SYSTEM_PROMPT_BASE
    assert "investigation_start and alert_sample are NOT" not in _SYSTEM_PROMPT_BASE


def test_gate_note_changes_when_investigation_start_missing():
    """Test gate note changes to unavailable version when investigation_start missing."""
    tools = (MockTool("other_tool"),)
    snapshot = TurnSnapshot(active_tools=tools, **_mock_snapshot_base())

    result = connected_integrations_block(snapshot)

    # Should contain the unavailable-case text
    assert "INVESTIGATION DISPATCH below" in result
    assert "investigation_start ALWAYS" not in result


def test_dispatch_block_absent_when_investigation_start_present():
    """Test dispatch blocks returns empty when investigation_start is present."""
    tools = (MockTool("investigation_start"), MockTool("other_tool"))
    snapshot = TurnSnapshot(active_tools=tools, **_mock_snapshot_base())

    result = investigation_dispatch_blocks(snapshot)

    assert result == ()


def test_unknown_active_tools_preserves_mandate():
    """Test empty active_tools preserves the available literal and does NOT contain INVESTIGATION DISPATCH below."""
    snapshot = TurnSnapshot(active_tools=(), **_mock_snapshot_base())

    result = connected_integrations_block(snapshot)

    # Should contain the available literal (inlined to catch drift)
    available_literal = (
        "This listing does NOT gate diagnostic→investigation. Cause/why / "
        "figure-out questions → assistant_handoff + gather + Want-me-to "
        "investigate offer. Explicit investigate/RCA/diagnose/analyze/"
        "root-cause verbs → investigation_start ALWAYS (even when this line "
        "is none).\n"
    )
    assert available_literal in result
    # Should NOT contain the dispatch override phrase
    assert "INVESTIGATION DISPATCH below" not in result
