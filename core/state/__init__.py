"""Shared agent state: the mutable per-session conversation store.

Holds the mutable per-session agent store reached through ``session.agent``
and the transcript-window compaction helpers.
"""

from __future__ import annotations

from core.state.agent_state import (
    MAX_CONVERSATION_MESSAGES,
    MAX_CONVERSATION_TURNS,
    AgentMessageRole,
    MutableAgentState,
)

__all__ = [
    "MAX_CONVERSATION_MESSAGES",
    "MAX_CONVERSATION_TURNS",
    "AgentMessageRole",
    "MutableAgentState",
]
