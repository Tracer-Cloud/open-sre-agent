"""Provider-specific rendering for runtime transcript messages."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

from core.llm.types import ToolCall
from core.messages.models import (
    AppRuntimeMessage,
    AssistantRuntimeMessage,
    ProviderMessage,
    RuntimeContent,
    RuntimeMessage,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)


def convert_to_llm_messages(llm: Any, messages: Sequence[RuntimeMessage]) -> list[ProviderMessage]:
    """Render runtime messages into provider-compatible message dictionaries."""

    provider_messages: list[ProviderMessage] = []
    for message in messages:
        provider_messages.extend(_provider_messages_for_runtime_message(llm, message))
    return provider_messages


def _provider_messages_for_runtime_message(
    llm: Any,
    message: RuntimeMessage,
) -> list[ProviderMessage]:
    if isinstance(message, UserRuntimeMessage):
        return [{"role": "user", "content": message.content}]
    if isinstance(message, AssistantRuntimeMessage):
        if message.provider_payload is not None:
            return [dict(message.provider_payload)]
        return [llm.build_assistant_message(message.content or "", list(message.tool_calls))]
    if isinstance(message, ToolResultRuntimeMessage):
        if message.provider_payloads:
            return [dict(payload) for payload in message.provider_payloads]
        return build_tool_result_messages(llm, list(message.tool_calls), list(message.results))
    if isinstance(message, AppRuntimeMessage):
        if not message.include_in_context:
            return []
        return [{"role": "user", "content": _provider_content_for_app_message(llm, message)}]
    return []


def _provider_content_for_app_message(llm: Any, message: AppRuntimeMessage) -> RuntimeContent:
    from core.llm.agent_llm_client import BedrockConverseAgentClient

    if isinstance(llm, BedrockConverseAgentClient):
        return _to_converse_text_blocks(message.content)
    return message.content


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


def build_synthetic_assistant_tool_call_message(
    llm: Any,
    tool_calls: list[ToolCall],
) -> ProviderMessage:
    """Build an assistant message that looks like the LLM requested these tool calls.

    This lets us inject pre-seeded tool results into the conversation in a format
    the LLM client already understands, without adding special-case handling.
    """
    from core.llm.agent_llm_client import (
        AnthropicAgentClient,
        BedrockConverseAgentClient,
        CLIBackedAgentClient,
        OpenAIAgentClient,
    )

    if isinstance(llm, BedrockConverseAgentClient):
        from core.llm.bedrock_converse import build_assistant_tool_use_message

        return cast("ProviderMessage", build_assistant_tool_use_message(tool_calls))

    if isinstance(llm, AnthropicAgentClient):
        content = [
            {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }
            for tc in tool_calls
        ]
        return {"role": "assistant", "content": content}

    if isinstance(llm, OpenAIAgentClient):
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

    # Fallback: plain text summary
    names = ", ".join(tc.name for tc in tool_calls)
    return {"role": "assistant", "content": f"I will start by querying: {names}"}


def build_assistant_message(llm: Any, response: Any) -> ProviderMessage:
    from core.llm.agent_llm_client import AnthropicAgentClient, BedrockConverseAgentClient

    if isinstance(llm, (AnthropicAgentClient, BedrockConverseAgentClient)):
        return cast("ProviderMessage", llm.build_assistant_message(response.raw_content))
    # Use raw_content when set — preserves provider-specific fields such as
    # Gemini's thought_signature that must be echoed back in the next request.
    if response.raw_content is not None:
        return response.raw_content  # type: ignore[no-any-return]
    result: dict[str, Any] = llm.build_assistant_message(response.content, response.tool_calls)
    return result


def build_tool_result_messages(
    llm: Any,
    tool_calls: list[ToolCall],
    results: list[Any],
) -> list[ProviderMessage]:
    from core.llm.agent_llm_client import AnthropicAgentClient, OpenAIAgentClient

    if isinstance(llm, AnthropicAgentClient):
        return [cast("ProviderMessage", llm.build_tool_result_message(tool_calls, results))]
    if isinstance(llm, OpenAIAgentClient):
        return cast("list[ProviderMessage]", llm.build_tool_result_messages(tool_calls, results))
    return [cast("ProviderMessage", llm.build_tool_result_message(tool_calls, results))]
