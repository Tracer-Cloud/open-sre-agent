"""Event contracts for the shared agent runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

AgentEventKind = Literal[
    "agent_start",
    "turn_start",
    "llm_start",
    "message_start",
    "message_end",
    "tool_start",
    "tool_end",
    "turn_end",
    "agent_end",
]

AgentEventCallback = Callable[[AgentEventKind, dict[str, Any]], None]

__all__ = ["AgentEventCallback", "AgentEventKind"]
