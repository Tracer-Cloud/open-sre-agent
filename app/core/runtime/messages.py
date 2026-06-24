"""Provider-specific message shaping for the shared LLM tool-calling runtime."""

from __future__ import annotations

import json
from typing import Any

from app.services.agent_llm_client import ToolCall


def build_synthetic_assistant_tool_call_message(
    llm: Any,
    tool_calls: list[ToolCall],
) -> dict[str, Any]:
    """Build an assistant message that looks like the LLM requested these tool calls.

    This lets us inject pre-seeded tool results into the conversation in a format
    the LLM client already understands, without adding special-case handling.
    """
    from app.services.agent_llm_client import (
        AnthropicAgentClient,
        BedrockConverseAgentClient,
        CLIBackedAgentClient,
        OpenAIAgentClient,
    )

    if isinstance(llm, BedrockConverseAgentClient):
        from app.services.bedrock_converse import build_assistant_tool_use_message

        return build_assistant_tool_use_message(tool_calls)

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
        return llm.build_assistant_message("", tool_calls)

    # Fallback: plain text summary
    names = ", ".join(tc.name for tc in tool_calls)
    return {"role": "assistant", "content": f"I will start by querying: {names}"}


def build_assistant_message(llm: Any, response: Any) -> dict[str, Any]:
    from app.services.agent_llm_client import AnthropicAgentClient, BedrockConverseAgentClient

    if isinstance(llm, (AnthropicAgentClient, BedrockConverseAgentClient)):
        return llm.build_assistant_message(response.raw_content)
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
) -> list[dict[str, Any]]:
    from app.services.agent_llm_client import AnthropicAgentClient, OpenAIAgentClient

    if isinstance(llm, AnthropicAgentClient):
        return [llm.build_tool_result_message(tool_calls, results)]
    if isinstance(llm, OpenAIAgentClient):
        return llm.build_tool_result_messages(tool_calls, results)
    return [llm.build_tool_result_message(tool_calls, results)]
