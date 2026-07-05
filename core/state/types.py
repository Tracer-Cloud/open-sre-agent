"""Shared type aliases for agent state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field
from typing_extensions import TypedDict

from config.strict_config import StrictConfigModel
from core.state.agent_state import AgentMessageRole

AgentMode = Literal["chat", "investigation", "agent_incident"]


class ChatMessage(TypedDict, total=False):
    role: AgentMessageRole
    content: str
    tool_calls: list[dict[str, Any]]
    # Tool-role messages (role: "tool") carry OpenAI-compatible correlation fields.
    tool_call_id: str
    name: str


class ChatMessageModel(StrictConfigModel):
    role: AgentMessageRole
    content: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
