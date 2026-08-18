"""LLM provider and model resolution shared by agent harness surfaces."""

from __future__ import annotations

from typing import Any


def default_llm_factory() -> Any:
    """Return the default agent LLM client.

    Uses a lazy import to avoid pulling in the full LLM stack at module load time.
    """
    from core.llm.factory import LLMRole, get_llm

    return get_llm(LLMRole.AGENT)


def default_reasoning_llm_factory() -> Any:
    """Return the default reasoning LLM client."""
    from core.llm.factory import LLMRole, get_llm

    return get_llm(LLMRole.REASONING)


__all__ = ["default_llm_factory", "default_reasoning_llm_factory"]
