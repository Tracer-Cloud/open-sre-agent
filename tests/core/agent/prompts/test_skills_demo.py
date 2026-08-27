"""Capability answers list skill demos instead of platform features."""

from __future__ import annotations

from core.agent_harness.prompts.action import build_action_system_prompt
from core.agent_harness.prompts.skills.loader import (
    clear_skills_caches,
    list_action_skills,
    load_skills_demo_block,
    load_skills_index,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def test_skills_demo_block_lists_frontmatter_demos_only() -> None:
    clear_skills_caches()
    demos = {skill.name: skill.demo for skill in list_action_skills() if skill.demo}

    assert demos == {
        "architecture-audit": "Audit this repo's architecture and give me a sequenced refactor plan",
        "github-ci-fix": "Find open PRs with failing CI and fix them",
        "github-security-fix": "Remediate the open Dependabot and CodeQL alerts",
        "morning-report": "Set up a weekday morning briefing with weather and news",
    }
    block = load_skills_demo_block()
    assert "ONLY the skill demos" in block
    assert "Do not list platform features" in block
    for prompt in demos.values():
        assert prompt in block


def test_agent_prompt_includes_skill_demos() -> None:
    clear_skills_caches()
    prompt = build_action_system_prompt(
        TurnSnapshot(
            text="what can you do?",
            conversation_messages=(),
            configured_integrations=(),
            configured_integrations_known=True,
            last_state=None,
            last_synthetic_observation_path=None,
            reasoning_effort=None,
        )
    )

    assert "ONLY the skill demos" in prompt
    assert "Audit this repo's architecture and give me a sequenced refactor plan" in prompt
    assert "Find open PRs with failing CI and fix them" in prompt
    assert "Remediate the open Dependabot and CodeQL alerts" in prompt
    assert "Set up a weekday morning briefing with weather and news" in prompt


def test_skills_index_routes_capability_questions_to_direct_answer() -> None:
    clear_skills_caches()
    index = load_skills_index()

    assert "what can you do" in index
    assert "Answer them directly" in index
