"""Prompt-side grounding providers for agent prompt assembly.

Distinct from ``core.agent_harness.grounding`` (caches / reference text).
"""

from __future__ import annotations

from core.agent_harness.prompts.grounding.environment import build_environment_block
from core.agent_harness.prompts.grounding.provider import (
    DefaultPromptContextProvider,
    load_llm_settings,
    supports_default_prompt_context,
)

__all__ = [
    "DefaultPromptContextProvider",
    "build_environment_block",
    "load_llm_settings",
    "supports_default_prompt_context",
]
