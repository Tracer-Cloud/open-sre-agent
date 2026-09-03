"""Tests for TURN INTERACTION facts injected into the action prompt."""

from __future__ import annotations

from core.agent_harness.prompts import build_action_system_prompt_envelope
from core.agent_harness.prompts.action.turn_interaction import turn_interaction_facts_block
from core.agent_harness.prompts.kernel.envelope import PromptBlockId
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def _snapshot(**overrides: object) -> TurnSnapshot:
    fields: dict[str, object] = {
        "text": "ship it",
        "conversation_messages": (),
        "configured_integrations": (),
        "configured_integrations_known": True,
        "reasoning_effort": None,
    }
    fields.update(overrides)
    return TurnSnapshot(**fields)  # type: ignore[arg-type]


def test_turn_interaction_facts_block_names_surface_goal_and_menu() -> None:
    text = turn_interaction_facts_block(
        _snapshot(
            prompt_surface="gateway",
            session_goal_attached=True,
            interactive_choice_available=False,
        )
    )
    assert "surface: gateway" in text
    assert "session_goal: attached" in text
    assert "ask_user_choice menu: unavailable" in text
    assert "only when the menu is available AND session_goal is none" in text


def test_action_envelope_includes_turn_interaction_block() -> None:
    envelope = build_action_system_prompt_envelope(
        _snapshot(
            prompt_surface="interactive_shell",
            interactive_choice_available=True,
        )
    )
    block = envelope.require_block(PromptBlockId.TURN_INTERACTION)
    assert "surface: interactive_shell" in block.content
    assert "ask_user_choice menu: available" in block.content
