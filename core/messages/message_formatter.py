"""Single boundary for all runtime <-> provider message conversion."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

from core.context_budget import strip_internal_message_markers
from core.llm.types import AgentLLMResponse, ToolCall
from core.messages.runtime_message_types import (
    AppRuntimeMessage,
    AssistantRuntimeMessage,
    MessageMetadata,
    ProviderMessage,
    RuntimeContent,
    RuntimeMessage,
    RuntimeMessageLike,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)


class MessageFormatter:
    """Converts runtime messages to/from provider-specific dicts for LLM invocation.

    ``normalize`` is a staticmethod — no llm needed.
    All other methods require an llm instance.
    """

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    @staticmethod
    def normalize(messages: Sequence[RuntimeMessageLike]) -> list[RuntimeMessage]:
        """Convert legacy provider dicts and typed messages into RuntimeMessage objects."""
        return [_coerce_runtime_message(m) for m in messages]

    def to_provider_messages(self, messages: Sequence[RuntimeMessage]) -> list[ProviderMessage]:
        """Render a RuntimeMessage sequence into provider dicts for llm.invoke.

        ``provider_payload``/``provider_payloads`` on a coerced RuntimeMessage retain
        internal ``_opensre_*`` markers (see ``_metadata_from_provider_message``), so
        the outbound render is stripped here rather than trusting each producer.
        """
        result: list[ProviderMessage] = []
        for message in messages:
            result.extend(self._for_runtime_message(message))
        return strip_internal_message_markers(result)

    def assistant_from_response(self, response: AgentLLMResponse) -> ProviderMessage:
        """Build the provider assistant-message payload from an LLM response."""
        from core.llm.transports.sdk.agent_clients import (
            AnthropicAgentClient,
            BedrockConverseAgentClient,
        )

        llm = self._llm
        if isinstance(llm, (AnthropicAgentClient, BedrockConverseAgentClient)):
            return cast("ProviderMessage", llm.build_assistant_message(response.raw_content))
        # raw_content carries provider-specific extras (e.g. Gemini's thought_signature)
        # that must be echoed back verbatim in the next request.
        if response.raw_content is not None:
            return response.raw_content  # type: ignore[no-any-return]
        result: dict[str, Any] = llm.build_assistant_message(response.content, response.tool_calls)
        return result

    def tool_results_from_execution(
        self,
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> list[ProviderMessage]:
        """Build provider tool-result payloads for a batch of tool calls."""
        from core.llm.transports.sdk.agent_clients import AnthropicAgentClient, OpenAIAgentClient

        llm = self._llm
        if isinstance(llm, AnthropicAgentClient):
            return [cast("ProviderMessage", llm.build_tool_result_message(tool_calls, results))]
        if isinstance(llm, OpenAIAgentClient) or _is_litellm_agent_client(llm):
            return cast(
                "list[ProviderMessage]", llm.build_tool_result_messages(tool_calls, results)
            )
        return [cast("ProviderMessage", llm.build_tool_result_message(tool_calls, results))]

    def synthetic_assistant_tool_call(self, tool_calls: list[ToolCall]) -> ProviderMessage:
        """Build a synthetic assistant message that looks like the LLM requested these tool calls.

        Used to inject pre-seeded tool results into the conversation without special-casing.
        """
        from core.llm.transports.sdk.agent_clients import (
            AnthropicAgentClient,
            BedrockConverseAgentClient,
            CLIBackedAgentClient,
            OpenAIAgentClient,
        )

        llm = self._llm

        if isinstance(llm, BedrockConverseAgentClient):
            from core.llm.transports.sdk.bedrock_converse import build_assistant_tool_use_message

            return cast("ProviderMessage", build_assistant_tool_use_message(tool_calls))

        if isinstance(llm, AnthropicAgentClient):
            return {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                    for tc in tool_calls
                ],
            }

        if isinstance(llm, OpenAIAgentClient) or _is_litellm_agent_client(llm):
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                    }
                    for tc in tool_calls
                ],
            }

        if isinstance(llm, CLIBackedAgentClient):
            return cast("ProviderMessage", llm.build_assistant_message("", tool_calls))

        names = ", ".join(tc.name for tc in tool_calls)
        return {"role": "assistant", "content": f"I will start by querying: {names}"}

    def to_assistant_runtime_message(self, response: AgentLLMResponse) -> AssistantRuntimeMessage:
        """Build a typed assistant transcript entry from an LLM response."""
        return AssistantRuntimeMessage(
            content=response.content or "",
            tool_calls=tuple(response.tool_calls),
            provider_payload=self.assistant_from_response(response),
        )

    def to_tool_result_runtime_message(
        self,
        tool_calls: list[ToolCall],
        results: list[Any],
        *,
        metadata: MessageMetadata | None = None,
    ) -> ToolResultRuntimeMessage:
        """Build a typed tool-result transcript entry from executed tool calls."""
        return ToolResultRuntimeMessage(
            tool_calls=tuple(tool_calls),
            results=tuple(results),
            provider_payloads=tuple(self.tool_results_from_execution(tool_calls, results)),
            metadata=dict(metadata or {}),
        )

    def _for_runtime_message(self, message: RuntimeMessage) -> list[ProviderMessage]:
        if isinstance(message, UserRuntimeMessage):
            return [{"role": "user", "content": message.content}]
        if isinstance(message, AssistantRuntimeMessage):
            if message.provider_payload is not None:
                return [dict(message.provider_payload)]
            return [
                self._llm.build_assistant_message(message.content or "", list(message.tool_calls))
            ]
        if isinstance(message, ToolResultRuntimeMessage):
            if message.provider_payloads:
                return [dict(payload) for payload in message.provider_payloads]
            return self.tool_results_from_execution(list(message.tool_calls), list(message.results))
        if isinstance(message, AppRuntimeMessage):
            if not message.include_in_context:
                return []
            return [{"role": "user", "content": self._app_message_content(message)}]
        return []

    def _app_message_content(self, message: AppRuntimeMessage) -> RuntimeContent:
        from core.llm.transports.sdk.agent_clients import BedrockConverseAgentClient

        if isinstance(self._llm, BedrockConverseAgentClient):
            return _to_converse_text_blocks(message.content)
        return message.content


def _coerce_runtime_message(message: RuntimeMessageLike) -> RuntimeMessage:
    if not isinstance(message, dict):
        return message

    role = message.get("role")
    if role == "user":
        return UserRuntimeMessage(
            content=message.get("content"),
            metadata=_metadata_from_provider_message(message),
        )
    if role == "assistant":
        return AssistantRuntimeMessage(
            content=message.get("content"),
            provider_payload=dict(message),
            metadata=_metadata_from_provider_message(message),
        )
    if role in {"tool", "toolResult", "tool_result"}:
        tool_name = str(message.get("name") or message.get("toolName") or "tool")
        tool_call_id = str(message.get("tool_call_id") or message.get("toolCallId") or tool_name)
        tool_call = ToolCall(id=tool_call_id, name=tool_name, input={})
        return ToolResultRuntimeMessage(
            tool_calls=(tool_call,),
            results=(message.get("content"),),
            provider_payloads=(dict(message),),
            metadata=_metadata_from_provider_message(message),
        )
    return AppRuntimeMessage(
        app_type="provider_message",
        content=json.dumps(message, default=str),
        include_in_context=False,
        details=dict(message),
        metadata=_metadata_from_provider_message(message),
    )


def _is_litellm_agent_client(llm: Any) -> bool:
    cls = type(llm)
    return (
        cls.__module__ == "core.llm.transports.litellm.clients"
        and cls.__name__ == "LiteLLMAgentClient"
    )


def _to_converse_text_blocks(content: RuntimeContent) -> RuntimeContent:
    if not isinstance(content, list):
        return content
    converted: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "text" and "text" in block:
            converted.append({"text": str(block["text"])})
        else:
            converted.append(dict(block))
    return converted


def _metadata_from_provider_message(message: ProviderMessage) -> MessageMetadata:
    return {key: value for key, value in message.items() if key.startswith("_opensre_")}


__all__ = ["MessageFormatter"]
