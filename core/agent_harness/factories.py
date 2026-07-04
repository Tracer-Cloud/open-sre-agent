"""Canonical factory functions for the agent harness."""

from __future__ import annotations

from typing import Any


def default_llm_factory() -> Any:
    """Return the default agent LLM client.

    Uses a lazy import to avoid pulling in the full LLM stack at module load time.
    """
    from core.llm import agent_llm_client

    return agent_llm_client.get_agent_llm()
