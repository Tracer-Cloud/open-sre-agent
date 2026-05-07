"""Framework-neutral types for chat / tool-calling LLM turns (issue #1358)."""

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
        raise NotImplementedError("BoundChatModel.invoke must be implemented by a concrete adapter")
