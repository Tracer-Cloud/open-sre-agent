"""Shared LLM tool-calling DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AgentLLMResponse:
    """Response from the agent LLM, with optional tool calls."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    # Raw provider message data for the next assistant turn.
    # Anthropic: list of content blocks (always populated).
    # OpenAI-compatible: dict with role/content/tool_calls, populated only when
    # provider-specific extras (e.g. Gemini's thought_signature) need to be
    # preserved; otherwise None and the assistant message is reconstructed via
    # build_assistant_message.
    raw_content: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


__all__ = ["AgentLLMResponse", "ToolCall"]
