"""Eagerly load the LLM client modules so ``core.llm.*`` loads as one snapshot."""

from __future__ import annotations


def preload_llm_clients() -> None:
    """Import the client modules at boot so a later code change can't leave a
    long-running process mixing old and new ``core.llm`` modules."""
    from core.llm import factory  # noqa: F401
    from core.llm.transports.sdk import agent_clients, llm_clients  # noqa: F401
