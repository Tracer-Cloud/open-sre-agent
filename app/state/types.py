"""Shared type aliases for agent state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field
from typing_extensions import TypedDict

from app.strict_config import StrictConfigModel

AgentMode = Literal["chat", "investigation"]


class ChatMessage(TypedDict, total=False):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[dict[str, Any]]
    # Tool-role messages (role: "tool") carry OpenAI-compatible correlation fields.
    tool_call_id: str
    name: str


class ChatMessageModel(StrictConfigModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
