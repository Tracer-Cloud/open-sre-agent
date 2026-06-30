"""Factory helpers for constructing runtime transcript messages."""

from __future__ import annotations

from typing import Any

from core.llm.types import ToolCall
from core.messages.convert_to_llm_messages import (
    build_assistant_message,
    build_synthetic_assistant_tool_call_message,
    build_tool_result_messages,
)
from core.messages.runtime_message_types import (
    AppRuntimeMessage,
    AssistantRuntimeMessage,
    MessageMetadata,
    RuntimeContent,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)


def user_runtime_message(content: RuntimeContent, **metadata: Any) -> UserRuntimeMessage:
    return UserRuntimeMessage(content=content, metadata=dict(metadata))


def app_runtime_message(
    app_type: str,
    content: RuntimeContent,
    *,
    include_in_context: bool = True,
    display: bool = True,
    details: Any = None,
    metadata: MessageMetadata | None = None,
) -> AppRuntimeMessage:
    return AppRuntimeMessage(
        app_type=app_type,
        content=content,
        include_in_context=include_in_context,
        display=display,
        details=details,
        metadata=dict(metadata or {}),
    )


def runtime_assistant_message(llm: Any, response: Any) -> AssistantRuntimeMessage:
    provider_payload = build_assistant_message(llm, response)
    return AssistantRuntimeMessage(
        content=getattr(response, "content", "") or "",
        tool_calls=tuple(getattr(response, "tool_calls", ()) or ()),
        provider_payload=provider_payload,
    )


def runtime_synthetic_assistant_tool_call_message(
    llm: Any,
    tool_calls: list[ToolCall],
    *,
    metadata: MessageMetadata | None = None,
) -> AssistantRuntimeMessage:
    return AssistantRuntimeMessage(
        content="",
        tool_calls=tuple(tool_calls),
        provider_payload=build_synthetic_assistant_tool_call_message(llm, tool_calls),
        metadata=dict(metadata or {}),
    )


def runtime_tool_result_message(
    llm: Any,
    tool_calls: list[ToolCall],
    results: list[Any],
    *,
    metadata: MessageMetadata | None = None,
) -> ToolResultRuntimeMessage:
    return ToolResultRuntimeMessage(
        tool_calls=tuple(tool_calls),
        results=tuple(results),
        provider_payloads=tuple(build_tool_result_messages(llm, tool_calls, results)),
        metadata=dict(metadata or {}),
    )
