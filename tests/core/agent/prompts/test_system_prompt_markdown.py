"""The action system prompt is loaded from bundled markdown."""

from __future__ import annotations

from pathlib import Path

from core.agent_harness.prompts.action import text as text_mod
from core.agent_harness.prompts.action.text import _PROMPT_FILENAME, _SYSTEM_PROMPT_BASE


def test_system_prompt_base_comes_from_markdown_file() -> None:
    path = Path(text_mod.__file__).with_name(_PROMPT_FILENAME)
    assert path.is_file()
    assert path.name == "opensre_system_prompt.md"
    assert path.read_text(encoding="utf-8") == _SYSTEM_PROMPT_BASE
