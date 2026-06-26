"""Shared types for the Pi-style agent runtime layers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.runtime.llm.agent_llm_client import ToolCall
from tools.registered_tool import RegisteredTool

type AgentMessage = dict[str, Any]


@dataclass
class AgentLoopResult:
    """Result returned by the pure think -> call tools -> observe loop."""

    messages: list[AgentMessage]
    final_text: str
    executed: list[tuple[ToolCall, Any]] = field(default_factory=list)
    hit_iteration_cap: bool = False


@dataclass
class AgentContext:
    """Runtime context consumed by an agent loop invocation."""

    llm: Any
    system_prompt: str
    messages: list[AgentMessage]
    tools: list[RegisteredTool]
    resolved_integrations: dict[str, Any]
    max_iterations: int


@dataclass
class AgentHarnessContext:
    """Snapshot used by harness providers to build one turn."""

    messages: list[AgentMessage]
    resolved_integrations: dict[str, Any]


class AgentSessionStore(Protocol):
    """Minimal persistence contract used by the harness layer."""

    def load_messages(self) -> list[AgentMessage]:
        """Return persisted agent messages for the active session."""

    def save_messages(self, messages: list[AgentMessage]) -> None:
        """Persist the complete message list after a turn."""


type SystemPromptProvider = str | Callable[[AgentHarnessContext], str]
type ToolProvider = Callable[[AgentHarnessContext], list[RegisteredTool]]
type IntegrationProvider = Callable[[], dict[str, Any]]
type LlmFactory = Callable[[], Any]

__all__ = [
    "AgentContext",
    "AgentHarnessContext",
    "AgentLoopResult",
    "AgentMessage",
    "AgentSessionStore",
    "IntegrationProvider",
    "LlmFactory",
    "SystemPromptProvider",
    "ToolProvider",
]
