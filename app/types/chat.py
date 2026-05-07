"""Framework-neutral types for chat / tool-calling LLM turns (issue #1358)."""

from __future__ import annotations

from typing import Any, Protocol

from typing_extensions import TypedDict


class ToolCallPayload(TypedDict):
    id: str
    name: str
    args: dict[str, Any]


class AssistantTurn(TypedDict, total=False):
    """One assistant generation: optional text plus optional tool calls."""

    content: str
    tool_calls: list[ToolCallPayload]


class BoundChatModel(Protocol):
    """Tool-bound or plain chat model that returns neutral assistant turns."""

    def invoke(self, messages: list[Any]) -> AssistantTurn:
        """Run one model invocation and return a framework-neutral turn."""
        ...
