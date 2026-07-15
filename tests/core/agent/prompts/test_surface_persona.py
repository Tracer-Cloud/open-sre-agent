"""The assistant prompt swaps to a Slack teammate persona on gateway turns."""

from __future__ import annotations

from core.agent_harness.prompts.assistant import build_assistant_system_prompt


def test_cli_surface_keeps_interactive_shell_persona() -> None:
    prompt = build_assistant_system_prompt("ref", "hist", surface="interactive_shell")
    assert "always call this surface the" in prompt  # interactive-shell terminology rule
    assert "AI production engineer on this team" not in prompt


def test_gateway_surface_uses_slack_teammate_persona() -> None:
    prompt = build_assistant_system_prompt("ref", "hist", surface="gateway")
    # Slack teammate voice: name + greeting, no terminal/CLI framing.
    assert "AI production engineer on this team" in prompt
    assert "introduce yourself" in prompt
    assert "always call this surface the" not in prompt


def test_gateway_reserves_three_tier_for_findings_only() -> None:
    prompt = build_assistant_system_prompt("ref", "hist", surface="gateway")
    assert "ONLY when reporting real findings" in prompt


def test_gateway_drops_slash_command_setup_guidance() -> None:
    prompt = build_assistant_system_prompt("ref", "hist", surface="gateway")
    # It must not push CLI slash-command setup at Slack users.
    assert "never tell them to run" in prompt


def test_gateway_prompt_includes_slack_layout_guidance() -> None:
    prompt = build_assistant_system_prompt("ref", "hist", surface="gateway")
    # Slack-specific layout: answer-first, scannable, real @mentions.
    assert "lead with the answer" in prompt
    assert "never invent mention tokens" in prompt


def test_cli_prompt_omits_slack_layout_guidance() -> None:
    prompt = build_assistant_system_prompt("ref", "hist", surface="interactive_shell")
    assert "lead with the answer" not in prompt


def test_gateway_preamble_is_slack_teammate_not_terminal() -> None:
    prompt = build_assistant_system_prompt("ref", "hist", surface="gateway")
    # The opening framing (highest salience) must not call it a terminal assistant.
    assert prompt.startswith("You are OpenSRE, an AI production engineer teammate")
    assert "terminal assistant" not in prompt
    assert "full-shell semantics" not in prompt
