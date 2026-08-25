"""The action system prompt is loaded from bundled markdown.

The file is the OpenSRE action *planner* STABLE base — compound turns, Phase 1b
handoff vs investigation_start, follow-up tags, slash mapping. It is not a
coding-agent / Codex clone (apply_patch, update_plan, AGENTS.md spec).
"""

from __future__ import annotations

from pathlib import Path

from core.agent_harness.prompts.action import text as text_mod
from core.agent_harness.prompts.action.text import _PROMPT_FILENAME, _SYSTEM_PROMPT_BASE

_CODING_AGENT_MARKERS = frozenset(
    {
        "coding_assistant_opener",
        "apply_patch",
        "update_plan",
        "agents_md_spec",
    }
)


def test_system_prompt_base_comes_from_markdown_file() -> None:
    path = Path(text_mod.__file__).with_name(_PROMPT_FILENAME)
    assert path.is_file()
    assert path.name == "opensre_system_prompt.md"
    assert path.read_text(encoding="utf-8") == _SYSTEM_PROMPT_BASE


def test_system_prompt_is_the_action_planner_not_a_coding_agent() -> None:
    prompt = _SYSTEM_PROMPT_BASE
    assert prompt.startswith("You plan actions for the OpenSRE interactive shell.")
    assert 'assistant_handoff(content="follow_up:prior_investigation")' in prompt
    assert "checkout is returning 502s" in prompt
    assert "check the health of my opensre and then show me all connected services" in prompt
    assert 'slash_invoke("/integrations", args=["list"])' in prompt
    assert _CODING_AGENT_MARKERS.isdisjoint(_prompt_markers(prompt))


def _prompt_markers(prompt: str) -> frozenset[str]:
    found: set[str] = set()
    if prompt.startswith("You are OpenSRE, a terminal-based SRE and coding assistant"):
        found.add("coding_assistant_opener")
    if "apply_patch" in prompt:
        found.add("apply_patch")
    if "update_plan" in prompt:
        found.add("update_plan")
    if "## AGENTS.md spec" in prompt:
        found.add("agents_md_spec")
    return frozenset(found)
