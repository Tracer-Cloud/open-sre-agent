"""Framework-neutral chat types and protocols (issues #1358, #1361).

Assistant turns and tool-call payloads are framework-agnostic. Message rows in
graph state are plain dicts shaped like ``ChatMessage`` in ``app/state/types.py``.
"""

from __future__ import annotations

from typing import Any, Protocol, Required, TypedDict


class ToolCallPayload(TypedDict):
    id: str
    name: str
    args: dict[str, Any]


class AssistantTurn(TypedDict, total=False):
    """One assistant generation: text content plus optional tool calls."""

    content: Required[str]
    tool_calls: list[ToolCallPayload]


class BoundChatModel(Protocol):
    """Tool-bound or plain chat model that returns neutral assistant turns."""

    def invoke(self, messages: list[Any]) -> AssistantTurn:
        """Run one model invocation and return a framework-neutral turn."""
        raise NotImplementedError
