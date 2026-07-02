"""Shared LLM tool-calling DTOs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMResponse:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


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


@runtime_checkable
class AgentLLMClient(Protocol):
    """The tool-calling LLM contract the shared ``Agent`` loop depends on.

    Deliberately narrow: only the two methods ``Agent`` invokes on its ``llm``
    (``tool_schemas`` and ``invoke``). Provider message-shaping (
    ``build_assistant_message`` / ``build_tool_result_message``) rides a
    separate seam (``core.messages.convert_to_llm_messages``) whose signatures
    diverge across providers, so it is intentionally excluded here.

    Implementers: the SDK clients (Anthropic / OpenAI / Bedrock-Converse /
    CLI-backed) and the canned ``_StaticToolCallLLM`` used by the explicit
    ``!``/``/slash`` paths.
    """

    @property
    def model_id(self) -> str | None:
        """The provider model identifier, used for context-budget sizing (may be None)."""

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        """Translate runtime tools into the provider's tool-schema payloads."""

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        """Run one provider request and return the assistant response."""


@runtime_checkable
class StreamingReasoningClient(Protocol):
    """The streaming LLM contract the conversational assistant answer depends on."""

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        """Stream the assistant reply as text chunks."""


__all__ = [
    "AgentLLMClient",
    "AgentLLMResponse",
    "LLMResponse",
    "StreamingReasoningClient",
    "ToolCall",
]
