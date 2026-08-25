"""The action system prompt is loaded from bundled markdown (Codex-style)."""

from __future__ import annotations

from pathlib import Path

from core.agent_harness.prompts.action import text as text_mod
from core.agent_harness.prompts.action.text import _PROMPT_FILENAME, _SYSTEM_PROMPT_BASE


def test_system_prompt_base_comes_from_markdown_file() -> None:
    path = Path(text_mod.__file__).with_name(_PROMPT_FILENAME)
    assert path.is_file()
    assert path.name == "opensre_system_prompt.md"
    assert path.read_text(encoding="utf-8") == _SYSTEM_PROMPT_BASE


def test_system_prompt_base_is_opensre_core_agent_prompt() -> None:
    prompt = _SYSTEM_PROMPT_BASE
    assert prompt.startswith("You are OpenSRE, a terminal-based SRE and coding assistant")
    assert "Goal-oriented planning (highest priority)" in prompt
    assert "Every tool call must advance that goal" in prompt
    assert "session_goal=true" in prompt
    assert "work_task_*" in prompt
    assert "GPT-5.2" not in prompt
    assert "Codex CLI" not in prompt
    assert "Codex refers" not in prompt
    assert "update_plan" not in prompt
    assert "AGENTS.md spec" in prompt
    assert "apply_patch" in prompt
