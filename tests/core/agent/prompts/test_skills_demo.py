"""Capability answers offer the stable getting-started choices."""

from __future__ import annotations

from core.agent_harness.prompts.action import build_action_system_prompt
from core.agent_harness.prompts.getting_started import (
    GETTING_STARTED_OPTIONS,
    load_getting_started_block,
)
from core.agent_harness.prompts.skills.loader import clear_skills_caches, load_skills_index
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def test_getting_started_block_lists_exact_options() -> None:
    assert GETTING_STARTED_OPTIONS == (
        "Explore a repo and analyze its CI/CD performance (recommended)",
        "Set up an agent that improves CI/CD reliability over time",
        "Connect OpenSRE to Slack and hand off DevOps chores for your team",
    )
    block = load_getting_started_block()
    assert "Use each option verbatim" in block
    assert "Or type your own answer..." in block
    for option in GETTING_STARTED_OPTIONS:
        assert f"- {option}" in block


def test_agent_prompt_includes_getting_started_options() -> None:
    clear_skills_caches()
    prompt = build_action_system_prompt(
        TurnSnapshot(
            text="what can you do?",
            conversation_messages=(),
            configured_integrations=(),
            configured_integrations_known=True,
            reasoning_effort=None,
        )
    )

    for option in GETTING_STARTED_OPTIONS:
        assert option in prompt
    assert "Remediate the open Dependabot and CodeQL alerts" not in prompt
    assert "Set up a weekday morning briefing with weather and news" not in prompt


def test_agent_prompt_combines_starter_options_with_selectable_choice_contract() -> None:
    clear_skills_caches()
    prompt = build_action_system_prompt(
        TurnSnapshot(
            text="Give me a demo",
            conversation_messages=(),
            configured_integrations=(),
            configured_integrations_known=True,
            reasoning_effort=None,
            prompt_surface="interactive_shell",
            interactive_choice_available=True,
        )
    )
    collapsed = " ".join(prompt.split())

    assert "ask_user_choice menu: available" in prompt
    assert "For a demo or getting-started request" in collapsed
    assert "assembled getting-started prompts as selectable options" in collapsed
    assert "call `ask_user_choice` with ONLY the getting-started options" in collapsed
    assert "that block supplies the menu options only" in collapsed
    for option in GETTING_STARTED_OPTIONS:
        assert option in prompt


def test_skills_index_routes_capability_questions_to_direct_answer() -> None:
    clear_skills_caches()
    index = load_skills_index()

    assert "what can you do" in index
    assert "Answer them directly" in index
