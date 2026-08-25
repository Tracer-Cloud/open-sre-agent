"""Action system prompt is markdown-backed; prose tool catalog no longer lives there.

``opensre_system_prompt.md`` (the core agent system prompt) does not enumerate
OpenSRE action tools in prose. Tool schemas on the call remain the catalog of
record. This module only pins that the markdown base is present and does not
reintroduce a stale prose catalog that would drift from the registry.
"""

from __future__ import annotations

from core.agent_harness.prompts.action.text import _SYSTEM_PROMPT_BASE


def test_action_system_prompt_has_no_prose_other_tools_catalog() -> None:
    """A resurrected 'Other tools:' list would drift from the registry again."""
    assert "Other tools:" not in _SYSTEM_PROMPT_BASE
