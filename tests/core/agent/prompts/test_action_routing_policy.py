"""Contracts for the stable action-agent routing policy."""

from __future__ import annotations

from core.agent_harness.prompts.action.routing_policy import ACTION_ROUTING_POLICY


def test_bare_incidents_handoff_instead_of_starting_an_investigation() -> None:
    prompt = " ".join(ACTION_ROUTING_POLICY.split())

    assert "bare alert, incident description, or symptom paste" in prompt
    assert 'assistant_handoff with evidence_kind="incident"' in prompt
    assert "never investigation_start" in prompt


def test_connected_services_compound_turn_uses_the_list_subcommand() -> None:
    prompt = " ".join(ACTION_ROUTING_POLICY.split())

    assert 'slash_invoke(command="/health", args=[])' in prompt
    assert 'slash_invoke(command="/integrations", args=["list"])' in prompt
