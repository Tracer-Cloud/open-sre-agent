"""The assistant prompt swaps to a Slack teammate persona on gateway turns."""

from __future__ import annotations

import re

import pytest

from config.constants.agent_identity import AGENT_NAME_ENV
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


def test_a_renamed_deployment_introduces_itself_by_that_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression: a Slack app called AcmeOps greeted people as OpenSRE."""
    monkeypatch.setenv(AGENT_NAME_ENV, "AcmeOps")

    prompt = build_assistant_system_prompt("ref", "hist", surface="gateway")

    assert prompt.startswith("You are AcmeOps, an AI production engineer teammate")
    # Both the core preamble and the Slack persona fragment carry the name.
    assert "You are AcmeOps, an AI production engineer on this team" in prompt
    assert "You are OpenSRE" not in prompt


def test_the_terminal_persona_is_renamed_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """One deployment, one agent name — the surface does not change who it is."""
    monkeypatch.setenv(AGENT_NAME_ENV, "AcmeOps")

    prompt = build_assistant_system_prompt("ref", "hist", surface="interactive_shell")

    assert prompt.startswith("You are AcmeOps, a production engineer")


def test_renaming_the_agent_does_not_rename_the_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bot is AcmeOps; the CLI it talks about is still `opensre`."""
    monkeypatch.setenv(AGENT_NAME_ENV, "AcmeOps")

    prompt = build_assistant_system_prompt("ref", "hist", surface="interactive_shell")

    assert "OpenSRE CLI" in prompt
    assert "AcmeOps CLI" not in prompt


def test_no_surface_leaves_blank_line_runs_from_empty_rule_slots() -> None:
    """CLI-only rule slots are empty on gateway turns; their separators must go too.

    Each slot contributes its own trailing separator, so an empty one adds
    nothing. A bare ``\\n\\n`` around an empty slot would waste prompt tokens and
    read as a missing section.
    """
    # Arrange / Act
    for surface in ("gateway", "interactive_shell"):
        prompt = build_assistant_system_prompt("ref", "hist", surface=surface)

        # Assert: no run of three or more newlines anywhere in the prompt.
        assert not re.search(r"\n{3,}", prompt), f"blank-line run in {surface} prompt"
