"""Contracts for the stable action-agent routing policy."""

from __future__ import annotations

from core.agent_harness.prompts.action.assemble import build_action_system_prompt_envelope
from core.agent_harness.prompts.action.routing_policy import ACTION_ROUTING_POLICY
from core.agent_harness.prompts.kernel.envelope import PromptBlockId
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def test_bare_incidents_handoff_instead_of_starting_an_investigation() -> None:
    prompt = " ".join(ACTION_ROUTING_POLICY.split())

    assert "bare alert, incident description, or symptom paste" in prompt
    assert 'assistant_handoff with evidence_kind="incident"' in prompt
    assert "never investigation_start" in prompt
    assert 'multi-line "Checkout API is returning HTTP 500s" paste' in prompt


def test_connected_services_compound_turn_uses_the_list_subcommand() -> None:
    prompt = " ".join(ACTION_ROUTING_POLICY.split())

    assert 'slash_invoke(command="/health", args=[])' in prompt
    assert 'slash_invoke(command="/integrations", args=["list"])' in prompt
    assert "never call it without the `list` argument" in prompt


def test_routing_policy_is_wired_into_the_action_envelope() -> None:
    """A markdown-only rewrite must not drop the dedicated routing block."""
    envelope = build_action_system_prompt_envelope(
        TurnSnapshot(
            text="checkout is returning 502s",
            conversation_messages=(),
            configured_integrations=(),
            configured_integrations_known=False,
            last_state=None,
            last_synthetic_observation_path=None,
            reasoning_effort=None,
        )
    )
    block = envelope.require_block(PromptBlockId.ACTION_ROUTING_POLICY)
    assert block.content == ACTION_ROUTING_POLICY
    assert ACTION_ROUTING_POLICY in envelope.render()
