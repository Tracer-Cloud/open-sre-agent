"""Shell-agent event contracts.

This module is intentionally UI-free. The shell agent emits these events;
runtime presentation code decides how to render them.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

AgentEventType = Literal[
    "agent_start",
    "agent_stop",
    "prompt_start",
    "prompt_interrupted",
    "prompt_error",
    "prompt_end",
]


@dataclass(frozen=True)
class AgentEvent:
    """Lifecycle event emitted by the shell agent."""

    type: AgentEventType
    text: str | None = None
    error: Exception | None = None


AgentEventSink = Callable[[AgentEvent], None]
AsyncAgentEventSink = Callable[[AgentEvent], Coroutine[Any, Any, None]]


__all__ = [
    "AgentEvent",
    "AgentEventSink",
    "AgentEventType",
    "AsyncAgentEventSink",
]
