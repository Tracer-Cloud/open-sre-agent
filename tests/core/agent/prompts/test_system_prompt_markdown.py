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
    assert "Do not search for AGENTS.md files or inspect the repository first" in (
        _SYSTEM_PROMPT_BASE
    )


def test_ask_user_choice_is_for_blocking_decisions_not_automated_follow_ups() -> None:
    """Optional next-step menus must not stall headless, scheduled, or /goal turns."""
    text = _SYSTEM_PROMPT_BASE
    collapsed = " ".join(text.split())
    assert "before work can continue" in collapsed
    assert "Do **not** call `ask_user_choice` just to park an optional follow-up" in collapsed
    assert "headless, scheduled, or gateway" in collapsed
    assert "when a `/goal` is attached" in collapsed
    assert "Always leave the user a selectable next step" not in text
    assert "Skip the menu on headless, scheduled, gateway, or" in collapsed
