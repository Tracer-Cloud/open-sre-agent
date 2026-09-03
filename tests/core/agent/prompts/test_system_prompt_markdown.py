"""The action system prompt is loaded from bundled markdown."""

from __future__ import annotations

from pathlib import Path

from core.agent_harness.prompts import system_prompt as prompt_mod
from core.agent_harness.prompts.action.text import _SYSTEM_PROMPT_BASE
from core.agent_harness.prompts.system_prompt import _PROMPT_FILENAME


def test_system_prompt_base_comes_from_markdown_file() -> None:
    path = Path(prompt_mod.__file__).with_name(_PROMPT_FILENAME)
    assert path.is_file()
    assert path.name == "opensre_system_prompt.md"
    assert path.read_text(encoding="utf-8") == _SYSTEM_PROMPT_BASE


def test_system_prompt_runs_explicit_commands_without_repository_probe() -> None:
    assert "execute it directly with the matching tool" in _SYSTEM_PROMPT_BASE
    assert "call `cli_exec` with the leading `opensre` prefix removed" in _SYSTEM_PROMPT_BASE
    assert "do not route it through `shell_run`" in _SYSTEM_PROMPT_BASE
    assert "Do not search for AGENTS.md files or inspect the repository first" in (
        _SYSTEM_PROMPT_BASE
    )


def test_finite_material_ambiguity_requires_selectable_clarification() -> None:
    text = _SYSTEM_PROMPT_BASE
    collapsed = " ".join(text.split())
    assert "Clarification is blocking whenever an underspecified request" in collapsed
    assert "materially different intents, goals, or execution paths" in collapsed
    assert "When TURN INTERACTION reports the menu is available" in collapsed
    assert "you MUST call `ask_user_choice`" in collapsed
    assert "numbered fallback only for required clarification" in collapsed
    assert "TURN INTERACTION reports the menu is unavailable" in collapsed


def test_demo_requests_require_selection_before_skill_resolution() -> None:
    collapsed = " ".join(_SYSTEM_PROMPT_BASE.split())
    assert "For a demo or getting-started request" in collapsed
    assert "assembled getting-started prompts as selectable options" in collapsed
    assert "takes precedence over any assembled getting-started instruction" in collapsed
    assert "that block supplies the menu options only" in collapsed
    assert "selection arrives verbatim as the next message" in collapsed
    assert "then resolve the selected skill or goal" in collapsed
    assert "Do not choose a goal or resolve a skill before the selection arrives" in collapsed


def test_finite_clarifications_are_batched_without_over_questioning() -> None:
    collapsed = " ".join(_SYSTEM_PROMPT_BASE.split())
    assert "batch them in one `ask_user_choice` call using the `questions` payload" in collapsed
    assert "Do not drip them across turns" in collapsed
    assert "when the user's intent is explicit" in collapsed
    assert "a safe default would not materially change the result" in collapsed
    assert "answers are open-ended rather than a small fixed set" in collapsed


def test_optional_choice_protections_follow_turn_interaction_facts() -> None:
    """Optional next-step menus follow TURN INTERACTION facts, not surface guessing."""
    text = _SYSTEM_PROMPT_BASE
    collapsed = " ".join(text.split())
    assert "Do **not** call `ask_user_choice` just to park an optional follow-up" in collapsed
    assert "when TURN INTERACTION says the menu is unavailable" in collapsed
    assert "session_goal` is attached" in collapsed
    assert "Always leave the user a selectable next step" not in text
    assert "TURN INTERACTION says the ask_user_choice menu is available" in collapsed
    assert "headless, scheduled, or gateway" not in collapsed


def test_proactive_messages_are_new_actionable_and_time_sensitive() -> None:
    collapsed = " ".join(_SYSTEM_PROMPT_BASE.split())
    assert "standing policy for unsolicited messages" in collapsed
    assert "verified information not previously shared" in collapsed
    assert "names a clear owner and next action" in collapsed
    assert "timing that can materially affect the outcome" in collapsed
    assert "Use a direct message for a blocker owned by a specific person or team" in collapsed
    assert "Broadcast only decisions, anomalies, or milestones" in collapsed
    assert "when the underlying state has not changed" in collapsed
    assert "Do not ask whether to adopt this policy" in collapsed
